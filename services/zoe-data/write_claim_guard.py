"""The write-claim guard: a reply that says it SAVED something is spoken only if something was saved.

Live 2026-10-12 (the trust problem): Zoe told the owner "I've added a work meeting to your calendar for tomorrow at 9 a.m."
There was no calendar row and not one tool call in the turn - the 4B brain narrated a write it never made. Another reply,
"I've added i don't know where you got that from…", sat over a list form nobody had confirmed. A spoken "done" that is not
true is worse than "I couldn't" - the owner stops trusting the whole assistant.

The rule, enforced at the ONE place every brain turn ends (``brain_dispatch.brain_streaming`` / ``brain_oneshot`` - voice,
chat, Telegram, LiveKit): a sentence that CLAIMS a write is let through only when ``write_receipts`` holds a receipt of that
kind for this user since the turn began (a list item / calendar event / reminder / note / timer that really landed - recorded
by the writers themselves, so a brain tool call that reached ``/api/system/intent-dispatch`` and succeeded counts, and one that
failed does not). Otherwise the sentence is replaced with an honest line and ``WRITE_CLAIM_BLOCKED`` is logged.

What counts as a claim is DATA, per language (``lexicons_data/write_claims.json``): claim verbs, target nouns, openers,
blockers (negation / irrealis / future). A new language or phrasing is a new row there, never a new regex here. A language
with no row simply claims nothing. Questions, offers ("want me to add it?"), refusals ("I couldn't add it") and promises
("I'll add it") are never claims.

Units: a reply is judged in UNITS - a sentence, or (once a sentence has run ``LEAD_TOKENS`` words) the part of it up to its next
comma / semicolon / colon / dash. A claim verb must sit within the first ``LEAD_TOKENS`` words of its unit, so streaming can
decide, from the first ``LEAD_TOKENS`` words alone, whether a unit can EVER become a claim: it holds a unit back until then (or
until the unit ends) and releases everything else at once. That bound is what makes streaming and one-shot output identical at
every delta cut - however the text is cut, the same units are judged on the same words. A claim verb counts in the active
("I've added …") and the completed passive ("… was added", "… has been added"); "is scheduled for Friday" describes the calendar
and is not a claim (only "is added" is, ``state_claim`` in the lexicon). Flag ``ZOE_WRITE_CLAIM_GUARD`` = ``enforce`` (default) | ``shadow`` (log only) |
``off``. The replay harness (``replay_isolation``) is never guarded - its writes are deliberately not committed.

Stdlib only; never raises into a turn.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator, Optional

import write_receipts

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).with_name("lexicons_data") / "write_claims.json"
ENV_FLAG = "ZOE_WRITE_CLAIM_GUARD"
# "did you add it?" is answered from what happened EARLIER: a recap may stand on a write from this recent a past.
RECAP_WINDOW_S = 1800.0
_SENTINEL_PREFIXES = ("__TOOL__:", "__THINKING__:")
# A claim verb must sit within this many words of the start of its unit (see the module docstring). 12 words is ~65 characters:
# the point at which the voice path's own first-unit clause break (``voice_tts._FIRST_UNIT_CLAUSE_MIN``) starts to apply anyway.
LEAD_TOKENS = 12


def mode() -> str:
    raw = (os.environ.get(ENV_FLAG) or "enforce").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return "off"
    return "shadow" if raw == "shadow" else "enforce"


# ── text folding / tokenising ──────────────────────────────────────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    if text.isascii():          # the common case on the hot path: NFKC / NFD / accent-stripping are the identity on ASCII
        return text.casefold()
    s =unicodedata.normalize("NFKC", text or "").replace("’", "'").replace("‘", "'").casefold()
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def tokenize(text: str) -> list[str]:
    return [t.strip("'") for t in re.findall(r"[a-z0-9']+", _fold(text)) if t.strip("'")]


def _phrase(text: str) -> str:
    return " ".join(tokenize(text))


# ── the lexicon ────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Lang:
    code: str
    subjects: frozenset
    second_person: frozenset
    openers: frozenset
    adverbs: frozenset
    verbs: dict           # token -> (kind, needs_target, state_claim)
    targets: dict         # kind -> tuple of phrases
    other_targets: tuple
    blockers: tuple
    recap_cues: tuple
    honest: str
    passive_done: frozenset = frozenset()     # "has been added": a perfect passive - a claim even with no target noun
    passive_past: frozenset = frozenset()     # "was added": a past passive - a claim when it names a target ("The restaurant was booked" does not)
    passive_state: frozenset = frozenset()    # "is added": a present-tense passive - a claim only for a ``state_claim`` verb
    clause_starts: frozenset = frozenset()    # words that open a new clause after a comma ("it", "that", …)
    joiners: tuple = ()                       # clause joiners as token tuples, longest first ("but", "so", "because")
    conditionals: frozenset = frozenset()     # "if" / "unless": a TRAILING one opens its own (blocked) clause, it never reaches back
    tag_questions: tuple = ()                 # a trailing "okay" / "right" / "vale": a TAG on a statement, not a question about the write
    question_openers: frozenset = frozenset() # words that open a genuine question ("did", "have", "should"): "Have I added it, okay?"
    ellipsis_objects: frozenset = frozenset() # what may follow a sentence-opening claim verb in a completion ellipsis ("Added THE milk")
    modifiers: frozenset = frozenset()        # ordinary adverbs that may sit before a claim verb ("definitely", "again")
    modifier_suffixes: tuple = ()             # a word ending in one is an adverb too ("-ly", "-mente"), once a subject stands before it

    def is_modifier(self, tok: str, *, by_suffix: bool = False) -> bool:
        if tok in self.adverbs or tok in self.modifiers:
            return True
        return by_suffix and any(len(tok) >= len(suf) + 3 and tok.endswith(suf) for suf in self.modifier_suffixes)

    @property
    def joiner_firsts(self) -> frozenset:
        return frozenset(j[0] for j in self.joiners)

    @property
    def hold_words(self) -> frozenset:
        return self.subjects | self.openers | self.adverbs | frozenset(self.verbs)


@lru_cache(maxsize=1)
def _langs() -> tuple:
    try:
        raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing / broken data file means NO claims are recognised, never a crash
        logger.warning("write_claim_guard: lexicon unreadable at %s - guard inactive", DATA_PATH)
        return ()
    out = []
    for code, lex in raw.items():
        if code.startswith("_") or not isinstance(lex, dict) or not lex.get("enabled"):
            continue

        def words(key: str) -> frozenset:
            return frozenset(w for w in (_phrase(x) for x in lex.get(key) or []) if w)

        def phrases(key: str) -> tuple:
            return tuple(p for p in (_phrase(x) for x in lex.get(key) or []) if p)

        verbs = {}
        for verb, spec in (lex.get("claim_verbs") or {}).items():
            tok = _phrase(verb)
            if tok:
                verbs[tok] = (str((spec or {}).get("kind") or "any"), bool((spec or {}).get("needs_target")),
                              bool((spec or {}).get("state_claim")))
        targets = {kind: phrases_ for kind, phrases_ in ((k, tuple(p for p in (_phrase(x) for x in v) if p))
                                                          for k, v in (lex.get("targets") or {}).items())}
        out.append(_Lang(
            code=code, subjects=words("subjects"), second_person=words("second_person"), openers=words("openers"),
            adverbs=words("adverbs"), verbs=verbs, targets=targets, other_targets=phrases("other_targets"),
            blockers=phrases("blockers"), recap_cues=phrases("recap_cues"), honest=str(lex.get("honest") or "").strip(),
            passive_done=words("passive_done"), passive_past=words("passive_past"), passive_state=words("passive_state"), clause_starts=words("clause_starts"),
            tag_questions=phrases("tag_questions"), question_openers=words("question_openers"),
            ellipsis_objects=words("ellipsis_objects"),
            modifiers=words("modifiers"), modifier_suffixes=tuple(sorted(phrases("modifier_suffixes"), key=len, reverse=True)),
            joiners=tuple(sorted((tuple(p.split()) for p in phrases("clause_joiners")), key=len, reverse=True)),
            conditionals=words("clause_conditionals"),
        ))
    return tuple(out)


def _has_phrase(padded: str, phrase: str) -> bool:
    return f" {phrase} " in padded


@dataclass(frozen=True)
class Claim:
    kinds: tuple          # known target kinds the sentence names; () = a generic "I've added it"
    lang: str
    honest: str


def _declarative_part(text: str) -> Optional[str]:
    """The statement a ``?``-ending unit really makes, or None when it is a genuine question. A TAG ("…, okay?", "…, right?",
    es "…, ¿vale?") questions only the tag: the clause before it is still an affirmative claim, so it is returned for judging.
    A question that opens with an interrogative word ("Have I added it, okay?") or "¿" is genuine and exempt."""
    if text.lstrip().startswith("¿"):
        return None
    body = text.rstrip().rstrip("?!. ").rstrip()
    cut = max(body.rfind(","), body.rfind(";"))
    if cut <= 0:
        return None
    tag, rest = _phrase(body[cut + 1:]), body[:cut]
    first = (tokenize(rest) or [""])[0]
    langs = _langs()
    if any(first in lang.question_openers for lang in langs):
        return None
    return rest if any(tag in lang.tag_questions for lang in langs) else None


def _completion_ellipsis(rest: list, has_target: bool, lang: "_Lang") -> bool:
    """A claim verb that OPENS its clause with nothing before it is Zoe's completion ("Added the milk.", "Added it.", "Added milk
    to the shopping list.") only when what follows reads as the thing written: nothing, a determiner / pronoun, or a target noun with
    no copula after it. "Scheduled maintenance starts at 2 PM" and "Added sugar is unhealthy" use the participle as an adjective."""
    if not lang.ellipsis_objects:
        return True                 # a language whose verbs conjugate ("añadí") has no bare-participle ambiguity
    if not rest or rest[0] in lang.ellipsis_objects:
        return True
    copulas = lang.passive_state | lang.passive_past | lang.passive_done
    return has_target and not any(t in copulas for t in rest)


def find_claim(sentence: str) -> Optional[Claim]:
    """The write this sentence CLAIMS to have made, or None. Pure. Every claim clause of the unit counts: the Claim names the
    kinds of ALL of them, so a receipt for one cannot vouch for another."""
    text = (sentence or "").strip()
    if not text:
        return None
    if text.endswith("?"):
        text = _declarative_part(text)
        if text is None:
            return None
    if not tokenize(text):
        return None
    for lang in _langs():
        # Blockers (negation, irrealis, future) and target nouns are judged in the CLAUSE of the claim verb only: "I've added
        # milk to your list, but I'm not sure about eggs" and "… as you wanted" still claim the milk (review, #2004).
        found: list = []            # the kinds each claiming clause names
        for offset, toks in _clauses(text, lang):
            padded = " " + " ".join(toks) + " "
            if any(_has_phrase(padded, b) for b in lang.blockers):
                continue
            for i, tok in enumerate(toks):
                if offset + i >= LEAD_TOKENS:
                    break               # streaming cannot see past here before it must release the unit (module docstring)
                spec = lang.verbs.get(tok)
                if spec is None:
                    continue
                verb_kind, needs_target, state_claim = spec
                pre = toks[max(0, i - 4):i]
                aux = _passive_aux(pre, lang)
                passive = aux in lang.passive_done or aux in lang.passive_past or (state_claim and aux in lang.passive_state)
                if not passive and any(p in lang.second_person for p in pre):
                    continue            # "you added it" - the user did it ("The milk you mentioned has been added": you is not the writer)
                active = all(p in lang.hold_words or p in lang.modifiers for p in pre) or _subject_then_modifiers(toks, i, lang)
                if not (active or passive):
                    continue
                kinds = tuple(kind for kind, phrases_ in lang.targets.items() if any(_has_phrase(padded, p) for p in phrases_))
                if not pre and not _completion_ellipsis(toks[i + 1:], bool(kinds), lang):
                    continue
                if not kinds:
                    if needs_target or any(_has_phrase(padded, o) for o in lang.other_targets):
                        continue
                    if not active and aux not in lang.passive_done and verb_kind != "any":
                        continue        # "The restaurant was booked solid": a past passive with no target noun claims no write
                    kinds = () if verb_kind == "any" else (verb_kind,)
                found.append(kinds)
                break                   # one claim per clause: its target nouns are already all in ``kinds``
        if found:
            specific = {k for kinds in found for k in kinds}
            ordered = tuple(k for k in lang.targets if k in specific) + tuple(sorted(specific - set(lang.targets)))
            return Claim(kinds=ordered, lang=lang.code, honest=lang.honest)
    return None


def _passive_aux(pre: list, lang: "_Lang") -> str:
    """The passive auxiliary just before a claim verb, looking back over adverbs ("was just added", "has been successfully
    added"); "" when there is none. A subject of any length may stand before it ("The appointment on Friday was added")."""
    for tok in reversed(pre):
        if tok in lang.passive_done or tok in lang.passive_past or tok in lang.passive_state:
            return tok
        if not lang.is_modifier(tok, by_suffix=True):
            return ""
    return ""


_SUBJECT_REACH = 6      # how far back from the claim verb a first-person subject may stand ("I have now definitely gone ahead and added")


def _subject_then_modifiers(toks: list, i: int, lang: "_Lang") -> bool:
    """True when the claim verb at ``toks[i]`` is led by a first-person subject through nothing but openers, claim words and
    modifiers - listed ones or any adverb by suffix ("I've definitely added", "we have quietly set"). The subject is what makes a
    suffix word safe to trust: "Emily added the milk" has none and stays someone else's write; "I think Mum added" is cut by
    "think", which is neither a hold word nor a modifier."""
    for j in range(i - 1, max(-1, i - 1 - _SUBJECT_REACH), -1):
        tok = toks[j]
        if tok in lang.subjects:
            return True
        if not (tok in lang.hold_words or lang.is_modifier(tok, by_suffix=True)):
            return False
    return False


_BREAK_RE = re.compile(r"[a-z0-9']+|[,;:—–]")


def _clauses(text: str, lang: "_Lang") -> list:
    """``(offset, tokens)`` for each clause of ``text`` - ``offset`` is how many words precede the clause in the unit. The word tokens split into clauses: at ``; : -`` always, at a comma that is followed by a new subject
    (``clause_starts`` / subjects / second person) or a joiner, and at every joiner word (``clause_joiners`` - "but", "so",
    "as", "because"… per language DATA; the joiner itself belongs to neither clause). A comma inside a list of items does
    not split ("I've added milk, eggs and bread to your list")."""
    items = [t.strip("'") if t[0] == "'" or t[-1] == "'" else t for t in _BREAK_RE.findall(_fold(text))]
    items = [t for t in items if t]
    starts = lang.clause_starts | lang.subjects | lang.second_person
    clauses: list = []
    cur: list = []
    seen = 0            # words consumed so far (joiners included) = the offset of the clause being built
    cur_off = 0
    i = 0

    def close() -> None:
        nonlocal cur, cur_off
        if cur:
            clauses.append((cur_off, cur))
        cur = []
        cur_off = seen

    while i < len(items):
        tok = items[i]
        if tok in ";:—–":
            close()
        elif tok == ",":
            nxt = next((t for t in items[i + 1:] if t not in ",;:—–"), "")
            if nxt in starts or nxt in lang.joiner_firsts:
                close()
        else:
            if tok in lang.conditionals and cur:
                # "I've added milk to your list, if that's okay": the hedge is a clause of its own and blocks only itself. A LEADING
                # "if" stays in its clause (and blocks it), so "If it's added, great" is still not a claim (review, #2004).
                close()
            joiner = next((len(j) for j in lang.joiners if items[i:i + len(j)] == list(j)), 0)
            if joiner:
                close()
                seen += joiner
                cur_off = seen
                i += joiner
                continue
            cur.append(tok)
            seen += 1
        i += 1
    close()
    return clauses


def is_recap_question(user_text: str) -> bool:
    """The user is asking about something done EARLIER ("did you add milk?"): a claim may stand on a recent past write."""
    if not (user_text or "").strip():
        return False
    padded = " " + " ".join(tokenize(user_text)) + " "
    return any(_has_phrase(padded, c) for lang in _langs() for c in lang.recap_cues)


# ── the verdict ────────────────────────────────────────────────────────────────────────────────────────────────────────

def supported(claim: Claim, user_id: Optional[str], started: float, *, user_text: str = "") -> bool:
    """True when a real write backs the claim: a receipt of every kind it names since ``started`` (a recap question reaches
    back ``RECAP_WINDOW_S``). A receipt is the ONLY evidence - not a tool's name: the ``phase=start`` sentinel arrives before the
    tool has finished, and a tool that failed leaves no receipt (review, #2004). A receipt made inside ANOTHER concurrent turn
    of the same user does not count (``write_receipts``)."""
    if is_recap_question(user_text):
        # "did you add milk?" asks about EARLIER turns: any of the user's recent writes, whichever turn made them.
        since = float(min(started, write_receipts.now() - RECAP_WINDOW_S))      # a plain float: no turn filter
        turn = None
    else:
        since, turn = started, getattr(started, "turn", None)
    if not claim.kinds:
        return write_receipts.has(user_id, since, turn=turn)
    return all(write_receipts.has(user_id, since, kind, turn=turn) for kind in claim.kinds)


def _log_blocked(claim: Claim, user_id: Optional[str], lane: str, action: str) -> None:
    # Counts and kinds only: the sentence is household conversation and stays out of the log.
    logger.warning("WRITE_CLAIM_BLOCKED action=%s lane=%s user=%s lang=%s kinds=%s reason=no_receipt",
                   action, lane, (user_id or "guest"), claim.lang, ",".join(claim.kinds) or "any")


# ── sentence splitting ─────────────────────────────────────────────────────────────────────────────────────────────────

_ABBREV_TAIL_RE = re.compile(r"(?:\b[a-z]\.[a-z]\.|\b(?:mr|mrs|ms|dr|st|vs|etc|no)\.)$", re.IGNORECASE)


_MERIDIEM_TAIL_RE = re.compile(r"\b[ap]\.m\.$", re.IGNORECASE)
_TERMINATOR_RE = re.compile(r"[.!?…]+[\"')\]]*\s|\n|[,;:—–]\s")
_TAIL_PUNCT_RE = re.compile(r"[.!?…,;:—–]+[\"')\]]*$")


def _sentence_end(buf: str, start: int = 0, unit_start: Optional[int] = None) -> Optional[int]:
    """Index just past the first COMPLETE unit at or after ``start`` in ``buf`` (terminator + the whitespace after it), else
    None. A unit ends at a sentence terminator, or at a comma / semicolon / colon / dash once the unit has run ``LEAD_TOKENS``
    words (so a long chatty run-up to a claim is cut where the claim's own clause begins). ``unit_start`` is where the unit
    began when that is before ``start`` (the streaming guard has already released the first part of it)."""
    for m in _TERMINATOR_RE.finditer(buf, start):
        end = m.end()
        if m.group()[0] in ",;:—–":
            if len(tokenize(buf[start if unit_start is None else unit_start:m.start()])) < LEAD_TOKENS:
                continue
            return end
        if _ABBREV_TAIL_RE.search(buf[:m.start() + 1]):
            # "at 9 a.m. tomorrow" goes on; "at 9 a.m. Anything else?" ends there (a capital starts the next sentence).
            ahead = buf[end:end + 1]
            if not (_MERIDIEM_TAIL_RE.search(buf[:m.start() + 1]) and ahead.isalpha() and ahead.isupper()):
                continue
        return end
    return None


def split_sentences(text: str) -> list[str]:
    """``text`` cut into the units the guard judges (see ``_sentence_end``); concatenated they give ``text`` back."""
    out, rest = [], text or ""
    while True:
        end = _sentence_end(rest)
        if end is None:
            break
        out.append(rest[:end])
        rest = rest[end:]
    if rest:
        out.append(rest)
    return out


def _could_become_claim(unit_so_far: str) -> bool:
    """Hold an unfinished unit until it is KNOWN it can never be a claim: it has fewer than ``LEAD_TOKENS`` complete words, or a
    claim verb sits among its first ``LEAD_TOKENS``, or it ends in punctuation (the boundary may be completing). The last word
    may be half-arrived ("I'v", "añ") and so is not counted."""
    toks = tokenize(unit_so_far)
    if unit_so_far and (unit_so_far[-1].isalnum() or unit_so_far[-1] in "'’"):      # any script: "añ" is half of "añadido"
        if toks:
            toks.pop()
    if len(toks) < LEAD_TOKENS or _TAIL_PUNCT_RE.search(unit_so_far):
        return True
    head = toks[:LEAD_TOKENS]
    return any(t in lang.verbs for lang in _langs() for t in head)


# ── the guard ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _replacement(sentence: str, claim: Claim) -> str:
    """The honest line in the claiming sentence's place, keeping the whitespace around it (a sentence cut across deltas can
    carry the boundary space as its LEADING whitespace)."""
    lead = sentence[:len(sentence) - len(sentence.lstrip())]
    trail = sentence[len(sentence.rstrip()):]
    return lead + claim.honest + trail


def guard_text(reply: str, user_id: Optional[str], started: float, *, user_text: str = "", lane: str = "brain") -> str:
    """The reply with every unsupported write claim replaced by the honest line (once). Unchanged when off / no claims."""
    m = mode()
    if m == "off" or not (reply or "").strip():
        return reply
    try:
        out, replaced = [], False
        for sentence in split_sentences(reply):
            claim = find_claim(sentence)
            if claim is None or supported(claim, user_id, started, user_text=user_text):
                out.append(sentence)
                continue
            _log_blocked(claim, user_id, lane, "shadow" if m == "shadow" else "replaced")
            if m == "shadow":
                out.append(sentence)
            elif not replaced and claim.honest:
                replaced = True
                out.append(_replacement(sentence, claim))
        return "".join(out) if out else reply
    except Exception as exc:  # noqa: BLE001 - never break a turn over bookkeeping
        logger.warning("write_claim_guard.guard_text failed (reply passed through): %s", exc)
        return reply


async def guard_stream(inner: AsyncIterator[str], user_id: Optional[str], started: float, *, user_text: str = "",
                       lane: str = "brain") -> AsyncIterator[str]:
    """Pass ``inner`` through, replacing an unsupported write-claim sentence. Sentinels pass untouched; text is held back only
    while a unit could still become a claim (``_could_become_claim``). The output equals ``guard_text`` of the whole reply however
    the deltas are cut."""
    m = mode()
    if m == "off":
        async for delta in inner:
            yield delta
        return
    held: list = []     # the deltas not yet released, as they arrived (an unchanged sentence goes out in the SAME pieces)
    head = ""           # the part of the CURRENT unit already released (it holds >= LEAD_TOKENS claim-free words)
    replaced = False

    def judge(sentence: str) -> Optional[str]:
        """None = release the sentence untouched; a string = release that instead (may be empty)."""
        nonlocal replaced, head, unit_clear
        whole = head + sentence
        head = ""
        unit_clear = False
        claim = find_claim(whole)
        if claim is None or supported(claim, user_id, started, user_text=user_text):
            return None
        _log_blocked(claim, user_id, lane, "shadow" if m == "shadow" else "replaced")
        if m == "shadow":
            return None
        if replaced or not claim.honest:
            return ""                      # a second claim in the same reply is dropped (guard_text does the same)
        replaced = True
        return _replacement(sentence, claim)

    def flush() -> list:
        """The pieces still held, judged as the final (possibly unfinished) unit of the stream."""
        buf = "".join(held)
        if not buf:
            return []
        verdict = judge(buf[cleared:]) if cleared < len(buf) else None
        pieces = list(held) if verdict is None else [buf[:cleared] + verdict]
        held.clear()
        return pieces

    unit_clear = False  # the current unit is already known claim-free (its first LEAD_TOKENS words held no claim verb)
    cleared = 0         # leading characters of ``held`` already judged clean (released once a whole piece is clean)

    try:
        async for delta in inner:
            if delta.startswith(_SENTINEL_PREFIXES):
                yield delta
                continue
            held.append(delta)
            while True:
                buf = "".join(held)
                # The released head of the unit counts too: where a unit ends (its word count) is then the same however the text
                # is cut.
                end = _sentence_end(head + buf, len(head) + cleared, 0 if head else cleared)
                if end is None:
                    break
                end -= len(head)
                verdict = judge(buf[cleared:end])
                if verdict is None:
                    cleared = end            # clean: stays in its ORIGINAL pieces
                    continue
                # An unsupported claim: release what was already clean, then the honest line in the sentence's place.
                out = buf[:cleared] + verdict
                held[:] = [buf[end:]] if buf[end:] else []
                cleared = 0
                if out:
                    yield out
            while held and len(held[0]) <= cleared:     # whole clean pieces go out exactly as they arrived
                piece = held.pop(0)
                cleared -= len(piece)
                yield piece
            tail = "".join(held)[cleared:]
            if tail and not (_TAIL_PUNCT_RE.search(tail) or (not unit_clear and _could_become_claim(head + tail))):
                # Known claim-free: the unit's first LEAD_TOKENS words hold no claim verb, so nothing later in it can be a
                # claim. (A tail that ends in punctuation is held: the boundary's whitespace may be one delta away.)
                unit_clear = True
                head += tail
                for piece in held:
                    yield piece
                held.clear()
                cleared = 0
        for piece in flush():
            yield piece
    except Exception:
        # The lane failed mid-stream (a failover / transport error): what was held back is real, already-generated text -
        # release it (judged like the end of a stream) before the error propagates, as the unguarded stream would have.
        for piece in flush():
            yield piece
        raise
    finally:
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass
