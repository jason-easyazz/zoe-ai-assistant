"""Evidence-bearing recall — WHEN a fact was told, and the user's own words.

Flag-dark ``ZOE_RECALL_EVIDENCE`` (default OFF, per-call read). Rendered by the ONE
packet builder ``routers/memories._build_memory_prompt_packet`` (Flue recall floor,
Flue ``recall_memory`` tool, core lane, memory.ts). Basis: verbatim evidence beside
distilled facts, +15.9 LoCoMo (docs/research/samantha-context-engineering-2026-09-29.md).
Both provenance rules fail closed:

* **Dates** are the capture time (``added_ts``, else ``added_at``) — "when the user
  told Zoe" only for per-turn writers. BATCH writers get no date (the nightly digest
  stores at ~03:00 over a 30 h lookback).
* **Quotes** only for writers proven to store the whole user utterance as
  ``source_excerpt``. Skybridge stores a name or the fact; Hindsight appends refs.

An edit (``supersedes_id``) was written by ``reviewed_by`` at edit time, so that
actor is the effective writer. Stdlib + ``memory_gate`` + ``time_utils`` only.
"""
from __future__ import annotations

import datetime
import re
import time
from typing import Any, Optional

EVIDENCE_ENV = "ZOE_RECALL_EVIDENCE"

#: Writers whose ``added_at`` is NOT when the user said it (offline batch passes).
BATCH_WRITERS = frozenset({
    "digest", "synthesis", "consolidation", "idle_consolidation", "music_digest",
})
#: Writers that pass the WHOLE user utterance as ``source_excerpt``
#: (memory_extractor.extract_and_ingest / memory_digest.run_turn_digest, chat +
#: voice, and expert_dispatch's explicit-teach extractor call).
QUOTABLE_WRITERS = frozenset({
    "chat_regex", "turn_digest", "voice_regex", "voice_turn_digest", "voice_fact",
})

EXCERPT_MAX_CHARS = 120
MAX_QUOTES = 3

# Fixed English names: strftime's %a/%b follow the process locale.
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WS_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z0-9']+")
_STOP = frozenset("the and for with that this you your user users has have had was are is his "
                  "her their they from about just know will been into".split())


def enabled() -> bool:
    """Per-call env read through the canonical bool parse (default OFF)."""
    from typed_env import env_bool

    return env_bool("ZOE_RECALL_EVIDENCE", False)


def effective_writer(meta: dict[str, Any]) -> str:
    meta = meta or {}
    if meta.get("supersedes_id") and meta.get("reviewed_by"):
        return str(meta.get("reviewed_by"))
    return str(meta.get("source") or "")


def row_epoch(meta: dict[str, Any]) -> Optional[float]:
    """Capture time in epoch seconds (``added_ts``, else ``added_at``), or None."""
    meta = meta or {}
    raw = meta.get("added_ts")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    iso = str(meta.get("added_at") or "").strip()
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def relative_day(days: int) -> str:
    """Calendar-day distance → words. Future (clock skew) reads as today."""
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{days // 30} months ago"
    years = days // 365
    return "1 year ago" if years == 1 else f"{years} years ago"


def render_date(epoch: float, *, now: Optional[float] = None) -> str:
    """``Mon 22 Sep, 8 days ago`` in the household timezone (``ZOE_TIMEZONE``);
    the year is added when it is not the current one."""
    from time_utils import zoe_timezone

    tz = zoe_timezone()
    when = datetime.datetime.fromtimestamp(epoch, tz)
    today = datetime.datetime.fromtimestamp(time.time() if now is None else now, tz)
    absolute = f"{_DAYS[when.weekday()]} {when.day} {_MONTHS[when.month - 1]}"
    if when.year != today.year:
        absolute += f" {when.year}"
    return f"{absolute}, {relative_day((today.date() - when.date()).days)}"


def date_suffix(meta: dict[str, Any], *, now: Optional[float] = None) -> str:
    """`` (Mon 22 Sep, 8 days ago)`` for a dateable row, else ""."""
    if effective_writer(meta) in BATCH_WRITERS:
        return ""
    epoch = row_epoch(meta)
    if epoch is None:
        return ""
    try:
        return f" ({render_date(epoch, now=now)})"
    except (OverflowError, OSError, ValueError):
        return ""


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2 and w not in _STOP}


def _one_line(text: str) -> str:
    """Single line, no characters that could close the quote or a block label."""
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = text.replace("[", "(").replace("]", ")")
    text = re.sub(r"[\"“”]", "'", text)
    return _WS_RE.sub(" ", text).strip()


def quote_for(meta: dict[str, Any], fact: str) -> str:
    """The user's own words behind ``fact`` (≤ EXCERPT_MAX_CHARS), or "".

    Only for quotable writers. A long utterance is narrowed to the sentence
    sharing the most content words with the fact (first on a tie), then cut at
    a word boundary. An excerpt that merely repeats the fact adds nothing."""
    if effective_writer(meta) not in QUOTABLE_WRITERS:
        return ""
    excerpt = _one_line(str((meta or {}).get("source_excerpt") or ""))
    if not excerpt:
        return ""
    if len(excerpt) > EXCERPT_MAX_CHARS:
        fact_words = _content_words(fact)
        best, best_score = excerpt, -1
        for sentence in _SENTENCE_RE.split(excerpt):
            score = len(_content_words(sentence) & fact_words)
            if score > best_score:
                best, best_score = sentence, score
        excerpt = best
        if len(excerpt) > EXCERPT_MAX_CHARS:
            cut = excerpt[: EXCERPT_MAX_CHARS - 1]
            excerpt = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:") + "…"
    if _WS_RE.sub(" ", excerpt.lower()).strip(" .!?") == _WS_RE.sub(" ", fact.lower()).strip(" .!?"):
        return ""
    return excerpt


def instruction_line(*, quotes: bool) -> str:
    """One line under the packet's authority rule. The quote clause rides only
    when a quote was rendered."""
    line = ("(Dates show when the user told you each note — use them for \"when\" "
            "questions; never guess a date that isn't shown.")
    if quotes:
        line += " Quote \"you said\" words only if asked what they said or if you're sure."
    return line + ")"


# ── Turn mark: the recall_memory TOOL's evidence gate ─────────────────────────
# The tool reaches /for-prompt over HTTP with the MODEL's query ("sister
# flight"), not the user's words, so the question shape is unknowable there.
# The Flue seam notes each real user's turn shape here (in-process — zoe-data is
# a single uvicorn worker); the tool call made during that turn reads it back.
# Every turn overwrites the mark, so a quote never leaks into the next turn.
_TURN_TTL_S = 300.0
_TURN_MAX = 256
_turn_marks: dict[str, tuple[bool, float]] = {}


def note_turn(user_id: str, message: str) -> None:
    """Record whether this Flue turn is evidence-shaped. No-op when off; never
    raises (a turn must not break on a quote hint)."""
    try:
        uid = (user_id or "").strip()
        if not uid or not enabled():
            return
        from memory_gate import is_evidence_question

        _turn_marks.pop(uid, None)
        _turn_marks[uid] = (is_evidence_question(str(message or "")), time.monotonic())
        while len(_turn_marks) > _TURN_MAX:
            _turn_marks.pop(next(iter(_turn_marks)))
    except Exception:  # noqa: BLE001
        return


def wants_quotes(message: str, user_id: str) -> bool:
    """Quote on this packet: the message itself is evidence-shaped, or the
    user's current Flue turn is (the tool path)."""
    from memory_gate import is_evidence_question

    if is_evidence_question(message or ""):
        return True
    mark = _turn_marks.get((user_id or "").strip())
    return bool(mark and mark[0] and time.monotonic() - mark[1] <= _TURN_TTL_S)
