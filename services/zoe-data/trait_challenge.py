"""trait_challenge - the owner hedges or denies a trait Zoe holds about them, and the row stops being served.

"Well, I'm not sure I'm a man of faith" used to change nothing: the turn digest only extracts what a
person SAYS they are, so a hedge produced no fact, the approved row ("User is a man of faith.") kept
standing, and the user-model card kept telling the owner who they were. A person's doubt about a
claim Zoe holds is evidence against it, and the cheapest correct response is to stop asserting it and
ask - not to guess the opposite.

* :func:`challenged_trait` (pure) reads one turn and returns the trait phrase the speaker hedges or
  denies of themself ("man of faith"), else None. It needs an article ("I'm not sure I'm a ...",
  "I'm not a ...", "I don't think I'm a ...", "I wouldn't say I'm a ..."): a bare state ("I'm not
  sure I'm ready") is not an identity claim.
* :func:`matching_rows` (pure) picks the approved rows that assert that trait: every content word
  of the trait is in the row, and the row is not itself a negation.
* :func:`apply_challenge` parks each match as ``pending`` (``MemoryService.flag_challenged``): no
  longer served on the card or in recall, still in the review queue, text and provenance untouched.
  An approve restores it; nothing is deleted and nothing is rewritten to the opposite.

Only the row's own user can do this (the digest passes the identified user), and a turn the speaker
check says is not them (``speaker_verified is False``) does nothing.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

MAX_ROWS = 5
SCAN_LIMIT = 2000

_STOP = frozenset({"a", "an", "the", "of", "is", "are", "user", "users", "s", "job", "role", "as", "to", "in"})
_NEG_IN_ROW = re.compile(r"\b(?:not|no longer|isn't|is not|never|doesn't|does not)\b", re.I)

_ART = r"(?:an?|the)\s+"
_ADV = r"(?:(?:really|actually|exactly|entirely|quite|sure)\s+)?"
_ME = r"i\s*(?:'m|am)\s+"
# the trait: up to five words, ended by punctuation or a conjunction
_TRAIT = r"(?P<t>[a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,4}?)(?=\s*(?:[,.;:!?]|$|\s(?:but|and|so|though|tho|or|really|anymore|any more|at all|either|honestly|to be honest)\b))"
_CUES = tuple(re.compile(p, re.I) for p in (
    rf"\bi(?:'m|\s+am)\s+not\s+(?:really\s+|entirely\s+|quite\s+)?sure\s+(?:that\s+)?{_ME}{_ART}{_TRAIT}",
    rf"\bi\s+(?:don't|do\s+not)\s+(?:really\s+)?(?:think|believe)\s+(?:that\s+)?{_ME}{_ART}{_TRAIT}",
    rf"\bi\s+wouldn't\s+say\s+(?:that\s+)?{_ME}{_ART}{_TRAIT}",
    rf"\bi(?:'m|\s+am)\s+not\s+{_ADV}{_ART}{_TRAIT}",
))


# words that make a denial hypothetical, quoted, reported or historical - checked only in the denial's OWN clause
_NOT_ASSERTED_BEFORE = re.compile(
    r"\b(?:what\s+if|if|suppose|supposing|imagine|pretend|hypothetically|used\s+to|once|when|whether|wonder|wondering|"
    r"(?:he|she|they|people|everyone|you)\s+(?:says?|said|thinks?|thought|reckons?|claims?|believes?|asked?|told\s+me)|"
    r"(?:ask|asked|asking|question|questions)\s+(?:me\s+)?(?:if|whether|that)?)\b", re.I)
# hypothetical framing that scopes over the WHOLE sentence, parenthetical commas included ("Suppose, for a moment, I'm not X");
# bare "you" is not a marker, and a contrastive "but" starts a fresh claim. "if" stays clause-scoped above ("If I'm tired, I'm not X").
_HYPOTHETICAL_SENTENCE = re.compile(
    r"\b(?:what\s+if|suppose|supposing|imagine|imagining|pretend|pretending|hypothetically|let's\s+say|say\s+that|"
    r"(?:ask|asked|asking)\s+(?:me\s+)?(?:if|whether)|wonder(?:ing)?\s+(?:if|whether)|whether)\b", re.I)
# a third party reporting the affirmation: not the speaker taking their denial back
_REPORTED = re.compile(
    r"(?<!\bi\s)\b(?:says?|said|saying|thinks?|thought|reckons?|claims?|believes?|insists?|tells?\s+me|told\s+me|"
    r"according\s+to|apparently)\b", re.I)
# ... or one the same sentence takes back: "I used to say I'm not X, but I am now" / "..., but actually I am"
_REVERSED_BARE = re.compile(r"\b(?:but\s+)?(?:actually\s+|in\s+fact\s+|really\s+|now\s+)?i\s+am\s*(?:[,;]|$)|\bthat\s+changed\b", re.I)
_REAFFIRM = re.compile(r"\bi\s*(?:'m|am)\s+(?!not\b)(?:now\s+|actually\s+|really\s+|in\s+fact\s+)?(?P<r>.*)|\bnow\s+i\s*(?:'m|am)\s+(?!not\b)(?P<r2>.*)", re.I)
# a row that says something about the owner: "User is a X." / "User's job/role: X" - never "User's sister is a X"
_ABOUT_OWNER = re.compile(
    r"^\s*(?:the\s+)?user(?:\s+(?!'s\b)[a-z]|'s\s+(?:job/role|job|role|occupation|profession|trade|identity|faith|religion|nature)\b)",
    re.I)
# where the owner's own statement ends and a second subject begins
_OTHER_SUBJECT = re.compile(r"[.!?;]\s|,?\s+and\s+(?:user's|his|her|their)\b|,\s*(?:user's|his|her|their)\b", re.I)


def _reversed(tail: str, trait: str) -> bool:
    """True when the rest of the sentence re-affirms THIS trait (not just any 'I'm now ...')."""
    if _REVERSED_BARE.search(tail):
        return True
    want = _content(trait)
    for m in _REAFFIRM.finditer(tail):
        rest = (m.group("r") or m.group("r2") or "").strip().lower()
        if _REPORTED.search(tail[: m.start()]):
            continue          # "but my husband says I'm a morning person": reported speech never reverses
        if want and want <= _content(rest):
            return True
    return False


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("‘", "'")).strip()


def _content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in _STOP and len(w) > 1}


def challenged_trait(turn: str) -> Optional[str]:
    """The identity trait the speaker hedges or denies of themself, lower-cased, else None."""
    t = _norm(turn)
    if not t or len(t.split()) > 60:
        return None
    for rx in _CUES:
        m = rx.search(t)
        if m:
            start = max((x.end() for x in re.finditer(r"[.!?;,\n]\s*", t[: m.start()])), default=0)
            if _NOT_ASSERTED_BEFORE.search(t[start:m.start()]):
                return None
            sent = max((x.end() for x in re.finditer(r"[.!?;\n]\s*", t[: m.start()])), default=0)
            scope = re.split(r"\bbut\b", t[sent:m.start()], flags=re.I)[-1]
            if _HYPOTHETICAL_SENTENCE.search(scope):
                return None
            tail = re.split(r"[.!?\n]", t[m.end():], maxsplit=1)[0]
            trait = m.group("t").strip().lower()
            if _reversed(tail, trait):
                return None
            return trait if _content(trait) else None
    return None


def matching_rows(trait: str, rows: Iterable[Any]) -> list[Any]:
    """Approved rows that assert ``trait`` of the user (every content word present, not a negation)."""
    want = _content(trait)
    if not want:
        return []
    out = []
    for r in rows:
        meta = getattr(r, "metadata", None) or {}
        text = getattr(r, "text", "") or ""
        if str(meta.get("status") or "approved") != "approved" or _NEG_IN_ROW.search(text):
            continue
        if not _ABOUT_OWNER.search(text):
            continue          # a relative's fact ("User's sister is a doctor") is not the owner's trait
        own = _OTHER_SUBJECT.split(text.strip(), maxsplit=1)[0]   # drop a second subject ("... and User's sister ...")
        if want <= _content(own):
            out.append(r)
    return out[:MAX_ROWS]


async def apply_challenge(user_id: str, turn: str, *, svc: Any = None,
                          speaker_verified: Optional[bool] = None) -> list[str]:
    """Flag the user's approved rows asserting the trait this turn doubts. Returns the flagged ids.
    Never raises into the turn."""
    try:
        if speaker_verified is False:
            return []
        trait = challenged_trait(turn)
        if not trait:
            return []
        if svc is None:
            from memory_service import get_memory_service  # type: ignore[import]

            svc = get_memory_service()
        rows = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
        flagged = []
        for r in matching_rows(trait, rows):
            if await svc.flag_challenged(r.id, user_id=user_id, utterance=turn):
                flagged.append(r.id)
        if flagged:
            logger.info("TRAIT_CHALLENGED user=%s rows=%d", user_id, len(flagged))
            try:
                from user_model_card import rebuild_user_model_card  # type: ignore[import]

                await rebuild_user_model_card(user_id)
            except Exception:  # noqa: BLE001 - the card re-checks liveness at serve time anyway
                pass
        return flagged
    except Exception as exc:  # noqa: BLE001
        logger.warning("trait_challenge failed (%s)", type(exc).__name__)
        return []
