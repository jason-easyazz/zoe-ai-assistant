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


_HALF_TAIL_RE = re.compile(r"(?:[\s-]+and[\s-]+a[\s-]+half|\.5)$", re.IGNORECASE)


def half_of(token: str) -> Optional[int]:
    """The whole part of "3 and a half" / "three and a half" / "3.5" (3), else None. Pure."""
    t = (token or "").strip().lower()
    m = _HALF_TAIL_RE.search(t)
    return number_of(t[:m.start()]) if m else None


def value_of(token: str) -> Optional[float]:
    """An age as a number: 7, 3.5 ("three and a half"), or None. Pure."""
    n = number_of(token)
    if n is not None:
        return float(n)
    h = half_of(token)
    return h + 0.5 if h is not None else None


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
# "3 and a half" / "three and a half" / "3.5": an age with its half (QA 6b: "wren is 3 and a half, not 3" was never read as a
# correction, so the stored "three" stayed)
_HALF = r"(?:(?:\s+|-)and(?:\s+|-)a(?:\s+|-)half|\.5)"
_NUMH = rf"{_NUM}{_HALF}"
_YEARS = r"(?:\s+(?:years?[ -]old|yrs?\s+old|yo))?"
_WORD = r"[A-Za-z][A-Za-z'’\-]{1,24}"
_REL = (r"(?:sister|brother|mum|mom|mother|dad|father|wife|husband|partner|son|daughter|boyfriend|girlfriend|nan|nana|grandma|"
        r"grandmother|grandpa|grandfather|aunt|auntie|uncle|cousin|niece|nephew|friend|boss)")
# "ashas swimming is on wednesday not thursday": a lazily typed possessive "<name>s <noun>" (the apostrophe dropped) is a subject too
_POSS = rf"(?!(?:the|my|our|this|that|his|her|its|their|your)\s){_WORD}['’]?s\s+{_WORD}"
_SUBJ = rf"(?P<subj>(?:my|our)\s+{_REL}|(?:the|my|our)\s+{_WORD}(?:\s+{_WORD})?|{_POSS}|{_WORD})"
_CUE = (r"(?P<cue>(?:(?:a\s+(?:small|quick)\s+)?correction|actually|actualy|actully|acutally|actualley|sorry|no|nope|wait|oh|hang\s+on|i\s+(?:misspoke|meant|said)|"
        r"(?:that(?:['’]s|\s+is|\s+was)\s+(?:wrong|incorrect|not\s+right)))[,.;:!\-\s]+)*")
_LEAD = rf"^\W*(?:(?:good\s+)?(?:morning|evening|afternoon)[,.\s]+)?(?:(?:and|also|oh\s+and|and\s+also)[,.\s]+)?{_CUE}"
_PAIR_RE = re.compile(_LEAD + rf"{_SUBJ}\s+(?:is|was|are)\s+(?:actually\s+|really\s+)?(?P<n>{_NUMH}|{_WORD}(?:\s+{_WORD}){{0,2}}|{_NUM}){_YEARS}[,;]?\s+"
                      rf"(?:and\s+)?not\s+(?P<o>{_NUMH}|{_WORD}(?:\s+{_WORD}){{0,2}}|{_NUM}){_YEARS}\W*(?:i\s+misspoke\W*)?$", re.IGNORECASE)
_AGE_NOW_RE = re.compile(_LEAD + rf"{_SUBJ}\s+(?:is|just\s+turned|turned|turns)\s+(?:(?P<pre>actually|now)\s+)?(?P<n>{_NUMH}|{_NUM}){_YEARS}"
                         rf"(?:\s+(?P<post>now))?\W*$", re.IGNORECASE)
# "its 76 for margaret, not 78": the value comes first, the subject after "for" (QA 6b)
_FOR_RE = re.compile(_LEAD + rf"(?:it['’]?s|that['’]?s|thats|it\s+is)\s+(?:actually\s+)?(?P<n>{_NUMH}|{_NUM}){_YEARS}\s+for\s+{_SUBJ}[,;]?\s+"
                     rf"(?:and\s+)?not\s+(?P<o>{_NUMH}|{_NUM}){_YEARS}\W*$", re.IGNORECASE)
# "kais 21 not 22" / "also kai 21 not 22": a lazily typed name (the possessive apostrophe and the "is" both dropped) and two numbers
# (QA round 7: this shape was never read, and the digest filed the stray "Kais: 21" as a person)
_BARE_RE = re.compile(_LEAD + rf"(?P<subj>{_WORD})\s+(?P<n>{_NUMH}|{_NUM}){_YEARS}[,;]?\s+(?:and\s+)?not\s+(?P<o>{_NUMH}|{_NUM}){_YEARS}\W*$",
                      re.IGNORECASE)
_PREP_RE = re.compile(r"^(?:on|at|in|by|for|around|about)\s+", re.IGNORECASE)


def _bare_value(v: str) -> str:
    """"on friday" -> "friday": a value's lead preposition is not part of the value (the stored row says "exam on Thursday")."""
    stripped = _PREP_RE.sub("", v.strip())
    return stripped if stripped and value_of(v) is None else v.strip()


_BARE_NOT_NAME = frozenset({"about", "around", "over", "under", "roughly", "maybe", "only", "just", "almost", "nearly", "still",
                            "page", "price", "cost", "costs", "score", "total", "temp", "monday", "tuesday", "wednesday", "thursday", "friday",
                            "saturday", "sunday", "number", "size", "age", "aged"})
_NOT_SUBJECT = frozenset({"it", "this", "that", "there", "here", "he", "she", "they", "we", "i", "you", "what", "who", "which", "the",
                          "a", "an", "my", "our", "your", "his", "her", "their", "so", "its", "zoe", "today", "tomorrow", "yesterday",
                          "tonight", "everything", "nothing", "something", "someone", "everyone", "anything"})


_OWNER_NOUN_RE = re.compile(r"^(?P<o>[A-Za-z][A-Za-z\-]{1,24}?)(?P<p>['’]s|s)\s+(?P<n>[A-Za-z][A-Za-z\-]{1,24})$")


def owner_noun(subject: str):
    """(owner, noun) for the subject "ashas swimming" / "Asha's swimming", else None. With an apostrophe the owner is exact ("asha");
    without one the typed word is kept whole ("ashas": a lazily typed possessive OR a name that ends in s, so a row decides which -
    see ``_owner_re``). The noun is lower-cased. Pure."""
    s = (subject or "").strip()
    if re.match(r"(?i)^(?:the|my|our|this|that|his|her|its|their|your)\s", s):
        return None
    m = _OWNER_NOUN_RE.match(s)
    if not m:
        return None
    lazy = not m.group("p").startswith(("'", "’"))
    word = m.group("o") + ("s" if lazy else "")
    return word[:1].upper() + word[1:].lower(), m.group("n").lower()


def _owner_re(owner: str):
    """A row names the owner: the word as typed, or (a lazily typed possessive) without its final s; never a longer word."""
    alts = [owner]
    if len(owner) > 3 and owner.lower().endswith("s") and not owner.lower().endswith("ss"):
        alts.append(owner[:-1])
    alt = "|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9'’])(?:{alt})(?:['’]?s)?(?![A-Za-z0-9])", re.IGNORECASE)


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
            new, old = _bare_value(m.group("n")), _bare_value(m.group("o"))
            if new.lower() == old.lower():
                continue
            cued = bool((m.group("cue") or "").strip()) or bool(re.search(r"\b(?:actually|really)\s+\S", sentence, re.IGNORECASE))
            both_num = value_of(new) is not None and value_of(old) is not None
            if not both_num and (not cued or not same_shape(new, old)):
                continue
            return Correction(subj, new, old, cued)
        m = _BARE_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            if subj.lower() in _NOT_SUBJECT or subj.lower() in _BARE_NOT_NAME:
                continue
            subj = re.sub(r"['’]s$", "", subj)      # "kais" stays as typed: a lazy possessive OR a name ending in s - the row decides
            if subj.lower() in _NOT_SUBJECT or subj.lower() in _BARE_NOT_NAME or subj.lower().rstrip("s") in _NOT_SUBJECT:
                continue                    # "thats 21 not 22" is no name
            new, old = m.group("n").strip(), m.group("o").strip()
            if value_of(new) is not None and value_of(old) is not None and value_of(new) != value_of(old):
                return Correction(subj, new, old, True)
            continue
        m = _FOR_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            new, old = m.group("n").strip(), m.group("o").strip()
            if subj.lower() not in _NOT_SUBJECT and value_of(new) != value_of(old):
                return Correction(subj, new, old, True)
            continue
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
    on = owner_noun(s)
    if on:
        return re.compile(rf"(?<![A-Za-z0-9'’]){re.escape(on[1])}(?![A-Za-z0-9])", re.IGNORECASE)   # the owner is checked in rewrite()
    m = re.match(rf"(?i)(?:my|our)\s+({_REL})$", s)
    if m:
        # the USER's relative only: "User's dad ...", "my dad ...", or a row that opens on it - never "Arjun's dad ..."
        return re.compile(rf"(?:^|\buser['’]s\s+|\b(?:my|our)\s+){re.escape(m.group(1))}(?![A-Za-z0-9])", re.IGNORECASE)
    m = re.match(r"(?i)(my|our)\s+(.+)$", s)
    if m:
        # the USER's own <noun> ("my birthday"): "User's birthday ..." or an unpossessed mention, never "Oliver's birthday ..."
        noun = re.escape(m.group(2))
        return re.compile(rf"(?:\buser['’]s\s+{noun}|(?<!['’]s\s)(?<![A-Za-z0-9'’]){noun})(?![A-Za-z0-9])", re.IGNORECASE)
    m = re.match(r"(?i)the\s+(.+)$", s)
    if m:
        s = m.group(1)          # "the pitch" -> any row that names the pitch ("User has a board pitch on Thursday")
    stem = s[:-1] if len(s) > 3 and s.lower().endswith("s") and not s.lower().endswith("ss") else None
    if stem:
        # a name typed with a stray s ("kais 21 not 22" for Kai): the word as typed, or without its s
        return re.compile(rf"(?<![A-Za-z0-9'’])(?:{re.escape(s)}|{re.escape(stem)}(?:s(?=:))?)(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(rf"(?<![A-Za-z0-9'’]){re.escape(s)}(?:s(?=:))?(?![A-Za-z0-9])", re.IGNORECASE)   # "Kais: 21" is Kai's row


_AGE_IN_ROW = rf"(?P<pre>\b(?:is|aged?|turned|turns|was)\s+|:\s*)(?P<num>{_NUM})(?P<half>{_HALF})?\b(?P<post>{_YEARS})"


def _row_value(am) -> Optional[float]:
    n = number_of(am.group("num"))
    return None if n is None else n + (0.5 if am.group("half") else 0.0)


def _render_value(v: float, like: str) -> str:
    """``v`` (7 or 3.5) written the way the row's number was ("three" -> "three and a half")."""
    whole = int(v)
    return number_like(whole, like) + (" and a half" if v - whole else "")


def rewrite(row_text: str, corr: Correction) -> Optional[str]:
    """``row_text`` with the corrected value, or None when the row is not the fact being corrected. Pure.

    The row must name the subject; a value pair needs the old value as a whole word AFTER the subject's first mention (the
    value is the subject's, not an earlier name's) and not already hold the new one; an age restatement rewrites the one age
    number that follows the subject ("Anaya is six", "Oliver, aged 24"). A row that mentions another number first is left alone."""
    text = row_text or ""
    sm = _subject_re(corr.subject).search(text)
    if not sm:
        return None
    on = owner_noun(corr.subject)
    if on and not _owner_re(on[0]).search(text):
        return None             # "Asha's swimming": a row about the swimming of someone else (or of the user) is not the one corrected
    tail = text[sm.end():]
    if corr.old is not None:
        old_n, new_n = value_of(corr.old), value_of(corr.new)
        if old_n is not None and new_n is not None:
            # an AGE pair: the subject's number, whatever its spelling ("24" == "twenty-four", "3" -> "3 and a half")
            for am in re.finditer(_AGE_IN_ROW, tail, re.IGNORECASE):
                if _row_value(am) == old_n:
                    return (text[:sm.end()] + tail[:am.start("num")] + _render_value(new_n, am.group("num"))
                            + tail[(am.end("half") if am.group("half") else am.end("num")):])
            return None
        old_re = re.compile(rf"(?<![A-Za-z0-9'’]){re.escape(corr.old)}(?![A-Za-z0-9'’])", re.IGNORECASE)
        if re.search(rf"(?<![A-Za-z0-9'’]){re.escape(corr.new)}(?![A-Za-z0-9'’])", tail, re.IGNORECASE):
            return None
        om = old_re.search(tail)
        if not om:
            return None
        new = corr.new[:1].upper() + corr.new[1:] if om.group(0)[:1].isupper() else corr.new
        return text[:sm.end()] + tail[:om.start()] + new + tail[om.end():]
    new_n = value_of(corr.new)
    if new_n is None:
        return None
    ages = list(re.finditer(_AGE_IN_ROW, tail, re.IGNORECASE))
    if not ages:
        return None
    am = ages[0]
    old_n = _row_value(am)
    if old_n is None or old_n == new_n:
        return None
    return (text[:sm.end()] + tail[:am.start("num")] + _render_value(new_n, am.group("num"))
            + tail[(am.end("half") if am.group("half") else am.end("num")):])


_FILLER = frozenset({"the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "for", "in", "on", "at", "and", "or", "user", "users",
                     "not", "it", "its", "that", "this", "has", "have", "had", "now", "year", "years", "old", "aged", "age", "yo",
                     "corrected", "actually", "turned", "turns"})


def invented_on_correction_turn(fact: str, user_message: str) -> bool:
    """True for a digest fact the correction turn cannot have said: it holds the corrected (new or old) value but names a field
    or word the turn never used. QA 6b: "actually its 76 for margaret, not 78" produced "User's blood pressure is 78" - the digest
    attached the stray number to an invented attribute of the user. The correction itself is applied to the stored rows
    (``apply``); the digest writes nothing new about a corrected value. Pure; False when the turn is not a correction."""
    corr = statement(user_message)
    if corr is None or not (fact or "").strip():
        return False
    values = [v for v in (corr.new, corr.old) if v]
    low = (fact or "").lower()
    subj = re.sub(r"(?i)^(?:the|my|our)\s+", "", corr.subject.strip()).lower()
    if subj and re.search(rf"(?<![a-z0-9]){re.escape(subj)}(?![a-z0-9])", low):
        return False        # a fact that names the corrected subject ("User's wife Margaret is 76") is about the correction, not invented
    if not any(re.search(rf"(?<![A-Za-z0-9]){re.escape(v.lower())}(?![A-Za-z0-9])", low) for v in values):
        return False
    said = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (user_message or "").lower().replace("'s", ""))}
    for w in re.findall(r"[a-z0-9]+", low.replace("'s", "")):
        if w in _FILLER or w.rstrip("s") in said:
            continue
        return True
    return False


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
