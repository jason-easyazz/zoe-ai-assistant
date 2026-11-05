"""dormant_threads - a quiet story the member has stopped talking about, touched again by something new, becomes ONE candidate.

Why (Samantha initiative item 15, lane C E4 and section 3.8 "Connections across months"): "I'm planning to repaint the shed" in
August and primer on the shopping list in October are the same story, and nothing joins them. The night mind keeps the story as a
thread and marks it ``quiet`` after ``QUIET_AFTER_DAYS``; nothing watches a quiet thread for the day it becomes useful again.

What (deterministic, CPU only, no model): ``candidates(threads, signals, today)`` intersects each DORMANT thread's anchors (the
``@names`` and distinctive words ``night_mind`` already keeps on the thread) with each new SIGNAL (a member's own turn today; a list
item or calendar entry later) using the night mind's own tokenisers:

* a dormant thread is ``quiet``, or open / changed / recurring with no mention for ``QUIET_AFTER_DAYS``;
* never a ``leave`` thread (health, grief, money, conflict, "don't bring that up", two ignored raises: ``leave_reason`` /
  ``raise_policy=leave`` / ``ignored_raises >= 2``), and never from a signal that itself falls in one of those topics;
* only a signal NEWER than the thread's last mention (the thread's own turns are not a reunion with itself);
* score = 2.5 per shared name + 1.5 per shared distinctive word; a signal that names somebody else than the thread's people scores
  0 (two different stories); a candidate needs ``THRESHOLD`` (3.0: a name and a word, or two words). Precision first: the field's
  warning (TriggerBench, PM-Bench) is that "always remind" buys recall with false alarms;
* at most ONE candidate per member per scan (the best score, then the most recent story).

A candidate is two pointers and a score: ``{thread_id, signal_kind, signal_ref, score, names, words}`` (counts, not the words). It is
computed on read and never stored, so a forget that erases the thread's observations (``night_store.erase_matching``) or the turn
leaves nothing behind.

``ZOE_DORMANT_THREADS`` = off (default) | shadow: ``scan`` reads the member's threads and logs one ``DORMANT_THREADS`` line of
counts and short ids. Delivery (an offer in the proactive pull queue, at most one a day, with the raise back-off) is the NEXT step
and is deliberately not built here: it needs the owner's shadow read first (lane C E4: precision-weighted, owner thumbs).
"""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

ENV = "ZOE_DORMANT_THREADS"
NAME_WEIGHT = 2.5
WORD_WEIGHT = 1.5
THRESHOLD = 3.0
MAX_PER_SCAN = 1
LIVE_STATUSES = ("open", "changed", "recurring")


def mode() -> str:
    """``off`` (default; unset or unrecognised) | ``shadow``. Per-call env read."""
    from typed_env import env_str

    return "shadow" if env_str(ENV, "off").strip().lower() == "shadow" else "off"


@dataclass(frozen=True)
class Signal:
    kind: str          # "turn" (a member's own words) | "list_item" | "calendar" | ...
    ref: str           # the pointer: a chat_messages id, a list item id, ...
    text: str          # read for this call only; never stored, never logged
    day: str           # ISO date of the signal


@dataclass(frozen=True)
class Candidate:
    thread_id: str
    signal_kind: str
    signal_ref: str
    score: float
    names: int
    words: int

    def as_dict(self) -> dict:
        return {"thread_id": self.thread_id, "signal_kind": self.signal_kind, "signal_ref": self.signal_ref, "score": self.score,
                "names": self.names, "words": self.words}


def _date(s: Any) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def is_leave(t: dict) -> bool:
    """A thread Zoe must never bring up on her own."""
    return bool(t.get("leave_reason")) or str(t.get("raise_policy") or "") == "leave" or int(t.get("ignored_raises") or 0) >= 2


def is_dormant(t: dict, today: _dt.date) -> bool:
    import night_mind

    status = str(t.get("status") or "")
    if status == "quiet":
        return True
    last = _date(t.get("last_day"))
    return status in LIVE_STATUSES and last is not None and (today - last).days >= night_mind.QUIET_AFTER_DAYS


def match(thread: dict, text: str) -> "tuple[float, int, int]":
    """``(score, shared names, shared words)`` of one thread against one signal's text. Pure."""
    import night_mind as nm

    anchors = str(thread.get("anchors") or "").split()
    t_names = {a[1:] for a in anchors if a.startswith("@")}
    t_words = {a for a in anchors if not a.startswith("@")} - nm._ANCHOR_STOP
    names = set(nm.names_in(text)) - nm._NOT_NAMES
    words = set(nm._words(text)) - nm._ANCHOR_STOP - names
    shared_names = names & t_names
    if names and t_names and not shared_names:
        return 0.0, 0, 0               # both name somebody, and not the same somebody: two different stories
    shared_words = words & t_words
    return NAME_WEIGHT * len(shared_names) + WORD_WEIGHT * len(shared_words), len(shared_names), len(shared_words)


def candidates(threads: Iterable[dict], signals: Sequence[Signal], today: _dt.date, *, threshold: float = THRESHOLD) -> "list[Candidate]":
    """The best candidate joining a dormant thread to a newer signal: at most ``MAX_PER_SCAN`` (one), a ceiling no caller can raise.
    Pure."""
    import night_mind as nm

    usable = [s for s in signals if s.text and not nm.topic_of([s.text])]     # a health / grief / money / conflict line is never a cue
    scored: "list[tuple[float, str, Candidate]]" = []
    for t in threads:
        tid = str(t.get("id") or "")
        if not tid or is_leave(t) or t.get("topic") or not is_dormant(t, today):
            continue
        last = _date(t.get("last_day"))
        best: "Optional[tuple[float, Candidate]]" = None
        for s in usable:
            sd = _date(s.day)
            if last is not None and (sd is None or sd <= last):
                continue
            score, n, w = match(t, s.text)
            if score >= threshold and (best is None or score > best[0]):
                best = (score, Candidate(tid, s.kind, s.ref, score, n, w))
        if best is not None:
            scored.append((best[0], str(t.get("last_day") or ""), best[1]))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c for _s, _d, c in scored[:MAX_PER_SCAN]]


async def scan(user_id: str, signals: Sequence[Signal], *, today: Optional[_dt.date] = None, backend: Any = None,
               force: bool = False) -> "list[Candidate]":
    """The member's candidates for today's signals (``shadow``, or ``force`` for the report): reads the member's threads only; logs
    counts and short ids. ``[]`` and no read when off. Never raises."""
    if not (force or mode() == "shadow") or not user_id:
        return []
    try:
        import night_store

        store = backend or night_store.get_backend()
        threads = await store.threads(user_id)
        day = today or _dt.date.today()
        found = candidates(threads, signals, day)
        logger.info("DORMANT_THREADS user=%s mode=shadow threads=%d dormant=%d signals=%d candidates=%d ids=%s", user_id, len(threads),
                    sum(1 for t in threads if is_dormant(t, day) and not is_leave(t)), len(signals), len(found),
                    ",".join(f"{c.thread_id[:8]}<-{c.signal_ref[:8]}" for c in found))
        return found
    except Exception as exc:  # noqa: BLE001 - shadow instrument: nothing depends on it
        logger.warning("DORMANT_THREADS user=%s failed kind=%s", user_id, type(exc).__name__)
        return []
