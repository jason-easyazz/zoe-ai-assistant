"""value_correction - a spoken correction of a stored value reaches the STORED rows, in any later session.

Why (QA round 5, 2026-11-08): "Oliver is twenty-five, not twenty-four" and "Anaya is 7 now" held inside the session (the brain
reads its own context) and then the OLD value won in a new session: both rows were served, and the correction was stored as a
meta-fact ("The user corrected a previous statement..."). ``correction_apply`` already rewrites a stored row from a correction,
but it is flag-dark (``ZOE_CORRECTION_APPLY``), reads a lead-cued value pair only, and ``maybe_fix_recent`` is bounded to the
SAME session. This is the always-on, post-turn tier of the same machinery (it reuses ``correction_apply.same_shape``,
``note_correction`` and the ``conversation_correction`` user-turn writer):

* ``statement(text)`` -> ``Correction(subject, new, old)`` for "Oliver is twenty-five, not twenty-four" / "correction: anaya is 7
  not 6" / "Anaya is 7 now" (``old`` None: an AGE restatement). A subject is a name, or "my <relation>". A bare value pair
  ("Tea, not coffee") is never read: the subject is what keeps it from rewriting an unrelated row.
* ``apply(...)`` finds the member's approved rows that name the subject AND hold the old value as a whole word (an age: the
  subject's age number), and supersedes each through ``MemoryService.review(edit)`` under the user-class writer
  ``conversation_correction`` with the member's own words as the anchor - the new row is USER-STATED, the old one is
  ``superseded`` (not served), nothing else is touched. Evidence only: no matching row, or more than ``MAX_ROWS``, changes nothing.
  The correction is also registered for the digest of an earlier turn that has not landed yet (``note_correction``).

Pure matching (``statement``, ``rewrite``); the write is one ``review`` per row. Never raises.
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

MAX_ROWS = 3            # the twin rows one utterance leaves behind; more than that is not one fact
MAX_WORDS = 18          # a correction is a short sentence
SCAN_LIMIT = 2000

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
         "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}
_WORD_N = {w: i for i, w in enumerate(_ONES)}
_WORD_N.update({w: n for n, w in _TENS.items()})


def number_of(token: str) -> Optional[int]:
    """0-120 from digits ("25") or words ("twenty-five", "twenty five", "six"); None for anything else. Pure."""
    t = " ".join((token or "").lower().replace("-", " ").split())
    if t.isdigit():
        n = int(t)
        return n if n <= 120 else None
    if t in _WORD_N:
        return _WORD_N[t]
    parts = t.split()
    if len(parts) == 2 and parts[0] in _WORD_N and parts[1] in _WORD_N and _WORD_N[parts[0]] in _TENS and 0 < _WORD_N[parts[1]] < 10:
        return _WORD_N[parts[0]] + _WORD_N[parts[1]]
    if t in ("one hundred", "a hundred", "hundred"):
        return 100
    return None


def number_like(n: int, like: str) -> str:
    """``n`` written the way ``like`` was ("24" -> digits, "twenty-four" -> hyphenated words, "twenty four" -> spaced words)."""
    if like.strip().isdigit():
        return str(n)
    if n >= 100:
        return "one hundred" if n == 100 else str(n)
    if n < 20:
        word = _ONES[n]
    else:
        word = _TENS[n - n % 10] + (("-" if "-" in like else " ") + _ONES[n % 10] if n % 10 else "")
    return word


_NUM = (r"(?:\d{1,3}|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?"
        r"|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen"
        r"|nineteen|one hundred)")
_YEARS = r"(?:\s+(?:years?[ -]old|yrs?\s+old|yo))?"
_WORD = r"[A-Za-z][A-Za-z'’\-]{1,24}"
_REL = (r"(?:sister|brother|mum|mom|mother|dad|father|wife|husband|partner|son|daughter|boyfriend|girlfriend|nan|nana|grandma|"
        r"grandmother|grandpa|grandfather|aunt|auntie|uncle|cousin|niece|nephew|friend|boss)")
_SUBJ = rf"(?P<subj>(?:my|our)\s+{_REL}|{_WORD})"
_CUE = (r"(?P<cue>(?:(?:a\s+(?:small|quick)\s+)?correction|actually|sorry|no|nope|wait|oh|hang\s+on|i\s+(?:misspoke|meant|said)|"
        r"(?:that(?:['’]s|\s+is|\s+was)\s+(?:wrong|incorrect|not\s+right)))[,.;:!\-\s]+)*")
_LEAD = rf"^\W*(?:(?:good\s+)?(?:morning|evening|afternoon)[,.\s]+)?{_CUE}"
_PAIR_RE = re.compile(_LEAD + rf"{_SUBJ}\s+(?:is|was|are)\s+(?:actually\s+|really\s+)?(?P<n>{_WORD}(?:\s+{_WORD}){{0,2}}|{_NUM}){_YEARS}[,;]?\s+"
                      rf"(?:and\s+)?not\s+(?P<o>{_WORD}(?:\s+{_WORD}){{0,2}}|{_NUM}){_YEARS}\W*(?:i\s+misspoke\W*)?$", re.IGNORECASE)
_AGE_NOW_RE = re.compile(_LEAD + rf"{_SUBJ}\s+(?:is|just\s+turned|turned|turns)\s+(?:(?P<pre>actually|now)\s+)?(?P<n>{_NUM}){_YEARS}"
                         rf"(?:\s+(?P<post>now))?\W*$", re.IGNORECASE)
_NOT_SUBJECT = frozenset({"it", "this", "that", "there", "here", "he", "she", "they", "we", "i", "you", "what", "who", "which", "the",
                          "a", "an", "my", "our", "your", "his", "her", "their", "so", "its", "zoe", "today", "tomorrow", "yesterday",
                          "tonight", "everything", "nothing", "something", "someone", "everyone", "anything"})


class Correction(NamedTuple):
    subject: str             # a name ("Oliver") or "my <relation>"
    new: str
    old: Optional[str]       # None: an age restated with no old value ("Anaya is 7 now")
    cued: bool = False


def statement(text: str) -> Optional[Correction]:
    """The subject-keyed value correction in ``text``, or None. Pure. Each sentence is read on its own; the first wins.

    A value pair ("<subject> is <new>, not <old>") needs the two values to be the same KIND of thing (``same_shape``: no
    clause, no verb) and, unless both are numbers, a correction cue ("correction", "actually", "sorry", "no", "I meant"...) -
    "Tom is tall, not short" with no cue and no number is a description. "<subject> is <N> now" restates an age."""
    from correction_apply import same_shape

    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Za-z])", " ".join((text or "").split())):
        if not sentence or len(sentence.split()) > MAX_WORDS:
            continue
        m = _PAIR_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            if subj.lower() in _NOT_SUBJECT:
                continue
            new, old = m.group("n").strip(), m.group("o").strip()
            if new.lower() == old.lower():
                continue
            cued = bool((m.group("cue") or "").strip()) or bool(re.search(r"\b(?:actually|really)\s+\S", sentence, re.IGNORECASE))
            both_num = number_of(new) is not None and number_of(old) is not None
            if not both_num and (not cued or not same_shape(new, old)):
                continue
            return Correction(subj, new, old, cued)
        m = _AGE_NOW_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            if subj.lower() in _NOT_SUBJECT:
                continue
            cued = bool((m.group("cue") or "").strip() or m.group("pre") or m.group("post")
                        or re.search(r"\bturn(?:ed|s)\b", sentence, re.IGNORECASE))
            if not cued:
                continue            # "Oliver is 25" with nothing saying it replaces a number is just a statement
            return Correction(subj, m.group("n").strip(), None, True)
    return None


def _subject_re(subject: str):
    """Whole-word match of the subject in a stored row: a name as is, "my <rel>" as the relation word ("User's dad ...")."""
    s = subject.strip()
    m = re.match(rf"(?i)(?:my|our)\s+({_REL})$", s)
    word = m.group(1) if m else s
    return re.compile(rf"(?<![A-Za-z0-9'’]){re.escape(word)}(?![A-Za-z0-9])", re.IGNORECASE)


_AGE_IN_ROW = rf"(?P<pre>\b(?:is|aged?|turned|turns|was)\s+)(?P<num>{_NUM})\b(?P<post>{_YEARS})"


def rewrite(row_text: str, corr: Correction) -> Optional[str]:
    """``row_text`` with the corrected value, or None when the row is not the fact being corrected. Pure.

    The row must name the subject; a value pair needs the old value as a whole word AFTER the subject's first mention (the
    value is the subject's, not an earlier name's) and not already hold the new one; an age restatement rewrites the one age
    number that follows the subject ("Anaya is six", "Oliver, aged 24"). A row that mentions another number first is left alone."""
    text = row_text or ""
    sm = _subject_re(corr.subject).search(text)
    if not sm:
        return None
    tail = text[sm.end():]
    if corr.old is not None:
        old_n, new_n = number_of(corr.old), number_of(corr.new)
        if old_n is not None and new_n is not None:
            # an AGE pair: the subject's number, whatever its spelling ("24" == "twenty-four")
            for am in re.finditer(_AGE_IN_ROW, tail, re.IGNORECASE):
                if number_of(am.group("num")) == old_n:
                    return text[:sm.end()] + tail[:am.start("num")] + number_like(new_n, am.group("num")) + tail[am.end("num"):]
            return None
        old_re = re.compile(rf"(?<![A-Za-z0-9'’]){re.escape(corr.old)}(?![A-Za-z0-9'’])", re.IGNORECASE)
        if re.search(rf"(?<![A-Za-z0-9'’]){re.escape(corr.new)}(?![A-Za-z0-9'’])", tail, re.IGNORECASE):
            return None
        om = old_re.search(tail)
        if not om:
            return None
        new = corr.new[:1].upper() + corr.new[1:] if om.group(0)[:1].isupper() else corr.new
        return text[:sm.end()] + tail[:om.start()] + new + tail[om.end():]
    new_n = number_of(corr.new)
    if new_n is None:
        return None
    ages = list(re.finditer(_AGE_IN_ROW, tail, re.IGNORECASE))
    if not ages:
        return None
    am = ages[0]
    old_n = number_of(am.group("num"))
    if old_n is None or old_n == new_n:
        return None
    return text[:sm.end()] + tail[:am.start("num")] + number_like(new_n, am.group("num")) + tail[am.end("num"):]


async def apply(user_message: str, user_id: str, session_id: Optional[str], *, svc=None,
                speaker_verified: Optional[bool] = None) -> int:
    """Supersede the member's stored rows the correction in ``user_message`` is about. Returns the rows rewritten (0 = nothing
    changed). An unverified speaker, an unidentified member or an opted-out member changes nothing. Never raises."""
    if speaker_verified is False:
        return 0
    corr = statement(user_message)
    if corr is None:
        return 0
    try:
        from correction_apply import SOURCE, _is_unidentified, note_correction

        if _is_unidentified(user_id):
            return 0
        try:
            import user_prefs

            if await user_prefs.is_memory_opted_out(user_id):
                return 0
        except Exception:  # noqa: BLE001
            pass
        if svc is None:
            from memory_service import get_memory_service

            svc = get_memory_service()
        uid = user_id.strip().lower()
        hits = []
        for r in await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT):
            meta = r.metadata or {}
            if str(meta.get("user_id") or meta.get("wing") or "").strip().lower() != uid:
                continue
            new_text = rewrite(r.text or "", corr)
            if new_text and new_text != r.text:
                hits.append((r, new_text))
        if corr.old is not None and session_id and not _is_unidentified(user_id):
            # the misheard / misspoken fact may not be stored yet (the post-turn digest of an earlier turn lands minutes later):
            # that digest rewrites the old value before it writes (correction_apply.correct_fact)
            try:
                from memory_extractor import _load_recent_user_messages

                earlier = await _load_recent_user_messages(user_id, session_id, user_message)
                note_correction(user_id, session_id, corr.new, corr.old, user_message, list(earlier or []))
            except Exception:  # noqa: BLE001
                pass
        if not hits or len(hits) > MAX_ROWS:
            return 0
        excerpt = " ".join(user_message.split())
        done = 0
        for r, new_text in hits:
            ref = await svc.review(
                r.id, decision="edit", edits=new_text, actor=SOURCE, note="spoken value correction (durable supersede)",
                source_excerpt=excerpt, anchor_text=excerpt, session_id=session_id,
                **({} if speaker_verified is None else {"speaker_verified": speaker_verified}))
            if ref is not None:
                done += 1
        if done:
            logger.info("VALUE_CORRECTION_APPLIED user=%s rows=%d", user_id, done)
        return done
    except Exception as exc:  # noqa: BLE001 - a correction never breaks the turn
        logger.warning("value_correction failed user=%s: %s", user_id, type(exc).__name__)
        return 0
