"""After the write-claim guard replaces a reply, the brain's NEXT turn is told the truth (``ZOE_WRITE_CLAIM_CORRECTION``).

QA 2026-11-05 (typed chat, one session): a timer request got a reply that claimed the timer with no tool call; the guard
replaced it for the user ("I didn't manage to add that; want me to try again?"). From then on every timer, alarm and reminder
request in that session came back with no tool call at all, and "yes, try again" failed the same way. A fresh session worked.

Why: the Flue sidecar keeps its OWN per-session history, built from what the model generated - so it holds the model's original
claim ("Setting a timer for 12 minutes now."), a write "done" with no tool call, and the 4B model copies that pattern on every
later write. The user saw the correction; the brain never did. (zoe-data's ``chat_messages`` holds the replacement line, which
can reach a brain as ``flue_fastpath_context`` rows or the core lane's ``history``; ``history_safe`` neutralises it there.)

The fix keeps the user-visible correction out of the brain's history as an instruction and hands the brain a FACT instead: the
first brain turn after a replaced claim carries one line before the user's words (``correction_note`` in
``lexicons_data/write_claims.json``, per language) saying the previous reply's write did not happen and that a change only
happens through a successful tool call. The line is not a delimited context block, so the sidecar's stale-block elision keeps it
next to the claim it corrects for the rest of the session. One pending note per session (newest kinds merged), bounded, 30 min.

``ZOE_WRITE_CLAIM_CORRECTION`` = ``1`` / ``true`` / ``on`` enables it; default OFF (nothing is noted, every message unchanged).
Stdlib only; nothing here raises into a turn.
"""
from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

ENV_FLAG = "ZOE_WRITE_CLAIM_CORRECTION"
_TTL_S = 1800.0
_MAX_SESSIONS = 256

# session id -> (lang code, kinds, noted at)
_PENDING: "OrderedDict[str, tuple[str, tuple, float]]" = OrderedDict()


def enabled() -> bool:
    from typed_env import env_bool

    return env_bool("ZOE_WRITE_CLAIM_CORRECTION", False)


def note(session_id: str, lang: str, kinds: Iterable[str]) -> None:
    """The guard replaced a claim of ``kinds`` in this session's reply. Never raises."""
    try:
        sid = (session_id or "").strip()
        if not sid or not enabled():
            return
        prev = _PENDING.pop(sid, None)
        merged = list(prev[1]) if prev and prev[0] == lang else []
        for k in kinds or ():
            if k not in merged:
                merged.append(k)
        _PENDING[sid] = (lang, tuple(merged), time.monotonic())
        while len(_PENDING) > _MAX_SESSIONS:
            _PENDING.popitem(last=False)
        logger.info("WRITE_CLAIM_CORRECTION noted session=%s kinds=%s", sid, ",".join(merged) or "any")
    except Exception:  # noqa: BLE001
        return


def take(session_id: str) -> str:
    """The correction line for this session's next brain turn (once: it is cleared), or ''. Never raises."""
    try:
        sid = (session_id or "").strip()
        if not sid or not enabled():
            return ""
        val = _PENDING.pop(sid, None)
        if not val:
            return ""
        lang, kinds, at = val
        if time.monotonic() - at > _TTL_S:
            return ""
        return render(lang, kinds)
    except Exception:  # noqa: BLE001
        return ""


def render(lang_code: str, kinds: tuple) -> str:
    """The language's ``correction_note`` naming what was claimed (``correction_kinds``), or '' when the language has none."""
    lex = _lexicon().get(lang_code) or {}
    template = str(lex.get("correction_note") or "").strip()
    if not template:
        return ""
    names = lex.get("correction_kinds") or {}
    parts = [str(names.get(k) or names.get("any") or k) for k in (kinds or ("any",))]
    what = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return template.replace("{what}", what)


def history_safe(text: str) -> str:
    """``text`` (a stored Zoe reply about to be shown to a brain as history) with every honest replacement line the guard can
    write swapped for the language's neutral ``history_marker``: a brain shown "I didn't manage to add that; want me to try again?"
    learns "tools fail"; the marker only says nothing was saved. Unchanged when the flag is off. Never raises."""
    if not text or not enabled():
        return text
    try:
        out = text
        for lex in _lexicon().values():
            marker = str(lex.get("history_marker") or "").strip()
            if not marker:
                continue
            lines = [str(lex.get("honest") or "")] + [str(v) for v in (lex.get("honest_by_kind") or {}).values()]
            for line in sorted({ln.strip() for ln in lines if ln and ln.strip()}, key=len, reverse=True):
                out = out.replace(line, marker)
        return out
    except Exception:  # noqa: BLE001
        return text


_CACHE: dict = {}


def _lexicon() -> dict:
    """The enabled language rows of the guard's lexicon (read once per path)."""
    try:
        import write_claim_guard

        path = str(write_claim_guard.DATA_PATH)
        if path not in _CACHE:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            _CACHE[path] = {k: v for k, v in raw.items()
                            if not k.startswith("_") and isinstance(v, dict) and v.get("enabled")}
        return _CACHE[path]
    except Exception:  # noqa: BLE001
        return {}


def reset() -> None:
    """Test hook."""
    _PENDING.clear()


def pending(session_id: str) -> Optional[tuple]:
    """Test / debug hook: the pending (lang, kinds) for a session, or None."""
    val = _PENDING.get((session_id or "").strip())
    return (val[0], val[1]) if val else None
