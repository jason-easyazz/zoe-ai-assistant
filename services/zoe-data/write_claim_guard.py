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

Streaming: text reaches the listener sentence by sentence. A sentence is HELD back only while it could still turn into a claim
(its words so far are all claim-opening words, or a claim verb has arrived); anything else flushes at once, so the first-audio
path is untouched for an ordinary reply. Flag ``ZOE_WRITE_CLAIM_GUARD`` = ``enforce`` (default) | ``shadow`` (log only) |
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
# Tools that read or look up and so can never have written anything. A claim that names no target ("I've added it") is
# given the benefit of the doubt only when some OTHER tool ran this turn.
READ_ONLY_TOOLS = frozenset({
    "get_time", "recall_memory", "activate_abilities", "get_weather", "show_calendar", "show_list", "list_reminders",
    "note_search", "web_search",
})


def mode() -> str:
    raw = (os.environ.get(ENV_FLAG) or "enforce").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return "off"
    return "shadow" if raw == "shadow" else "enforce"


# ── text folding / tokenising ──────────────────────────────────────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").replace("’", "'").replace("‘", "'").casefold()
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
    verbs: dict           # token -> (kind, needs_target)
    targets: dict         # kind -> tuple of phrases
    other_targets: tuple
    blockers: tuple
    recap_cues: tuple
    honest: str

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
                verbs[tok] = (str((spec or {}).get("kind") or "any"), bool((spec or {}).get("needs_target")))
        targets = {kind: phrases_ for kind, phrases_ in ((k, tuple(p for p in (_phrase(x) for x in v) if p))
                                                          for k, v in (lex.get("targets") or {}).items())}
        out.append(_Lang(
            code=code, subjects=words("subjects"), second_person=words("second_person"), openers=words("openers"),
            adverbs=words("adverbs"), verbs=verbs, targets=targets, other_targets=phrases("other_targets"),
            blockers=phrases("blockers"), recap_cues=phrases("recap_cues"), honest=str(lex.get("honest") or "").strip(),
        ))
    return tuple(out)


def _has_phrase(padded: str, phrase: str) -> bool:
    return f" {phrase} " in padded


@dataclass(frozen=True)
class Claim:
    kinds: tuple          # known target kinds the sentence names; () = a generic "I've added it"
    lang: str
    honest: str


def find_claim(sentence: str) -> Optional[Claim]:
    """The write this sentence CLAIMS to have made, or None. Pure."""
    text = (sentence or "").strip()
    if not text or text.endswith("?"):
        return None
    toks = tokenize(text)
    if not toks:
        return None
    padded = " " + " ".join(toks) + " "
    for lang in _langs():
        if any(_has_phrase(padded, b) for b in lang.blockers):
            continue
        for i, tok in enumerate(toks):
            spec = lang.verbs.get(tok)
            if spec is None:
                continue
            verb_kind, needs_target = spec
            pre = toks[max(0, i - 4):i]
            if any(p in lang.second_person for p in pre):
                continue            # "you added it" - the user did it
            active = all(p in lang.hold_words for p in pre)
            passive = bool(pre) and (pre[-1] == "been" or (len(pre) > 1 and pre[-2] == "been" and pre[-1] in lang.adverbs))
            if not (active or passive):
                continue
            kinds = tuple(kind for kind, phrases_ in lang.targets.items() if any(_has_phrase(padded, p) for p in phrases_))
            if not kinds:
                if needs_target or any(_has_phrase(padded, o) for o in lang.other_targets):
                    continue
                kinds = () if verb_kind == "any" else (verb_kind,)
            return Claim(kinds=kinds, lang=lang.code, honest=lang.honest)
    return None


def is_recap_question(user_text: str) -> bool:
    """The user is asking about something done EARLIER ("did you add milk?"): a claim may stand on a recent past write."""
    if not (user_text or "").strip():
        return False
    padded = " " + " ".join(tokenize(user_text)) + " "
    return any(_has_phrase(padded, c) for lang in _langs() for c in lang.recap_cues)


# ── the verdict ────────────────────────────────────────────────────────────────────────────────────────────────────────

def supported(claim: Claim, user_id: Optional[str], started: float, *, user_text: str = "", tools_run: frozenset = frozenset()) -> bool:
    """True when a real write backs the claim: a receipt of every kind it names since ``started`` (a recap question reaches
    back ``RECAP_WINDOW_S``). A claim that names no target is also backed by any non-read tool having run this turn."""
    since = started
    if is_recap_question(user_text):
        since = min(started, write_receipts.now() - RECAP_WINDOW_S)
    if not claim.kinds:
        return write_receipts.has(user_id, since) or bool(tools_run - READ_ONLY_TOOLS)
    return all(write_receipts.has(user_id, since, kind) for kind in claim.kinds)


def _log_blocked(claim: Claim, user_id: Optional[str], lane: str, action: str) -> None:
    # Counts and kinds only: the sentence is household conversation and stays out of the log.
    logger.warning("WRITE_CLAIM_BLOCKED action=%s lane=%s user=%s lang=%s kinds=%s reason=no_receipt",
                   action, lane, (user_id or "guest"), claim.lang, ",".join(claim.kinds) or "any")


# ── sentence splitting ─────────────────────────────────────────────────────────────────────────────────────────────────

_ABBREV_TAIL_RE = re.compile(r"(?:\b[a-z]\.[a-z]\.|\b(?:mr|mrs|ms|dr|st|vs|etc|no)\.)$", re.IGNORECASE)


_MERIDIEM_TAIL_RE = re.compile(r"\b[ap]\.m\.$", re.IGNORECASE)
_TERMINATOR_RE = re.compile(r"[.!?…]+[\"')\]]*\s|\n")


def _sentence_end(buf: str, start: int = 0) -> Optional[int]:
    """Index just past the first COMPLETE sentence at or after ``start`` in ``buf`` (terminator + the whitespace after it),
    else None."""
    for m in _TERMINATOR_RE.finditer(buf, start):
        end = m.end()
        if _ABBREV_TAIL_RE.search(buf[:m.start() + 1]):
            # "at 9 a.m. tomorrow" goes on; "at 9 a.m. Anything else?" ends there (a capital starts the next sentence).
            ahead = buf[end:end + 1]
            if not (_MERIDIEM_TAIL_RE.search(buf[:m.start() + 1]) and ahead.isalpha() and ahead.isupper()):
                continue
        return end
    return None


def split_sentences(text: str) -> list[str]:
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


def _could_become_claim(tail: str) -> bool:
    """Hold an unfinished sentence while its words so far are all claim-opening words (or a claim verb has arrived)."""
    toks = tokenize(tail)
    if not toks:
        return True
    partial = None
    if tail and not tail[-1].isspace() and re.match(r"[A-Za-z0-9'’]", tail[-1]):
        partial = toks.pop()                 # the last word may be half-arrived ("I'v")
    for lang in _langs():
        hold = lang.hold_words
        if any(t in lang.verbs for t in toks):
            return True
        if len(toks) <= 5 and all(t in hold for t in toks):
            if partial is None or any(w.startswith(partial) for w in hold):
                return True
    return False


def _tool_name(delta: str) -> str:
    try:
        body = json.loads(delta[len("__TOOL__:"):])
        if isinstance(body, dict) and body.get("phase") in ("start", "args"):
            return str(body.get("name") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


# ── the guard ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def guard_text(reply: str, user_id: Optional[str], started: float, *, user_text: str = "", lane: str = "brain",
               tools_run: frozenset = frozenset()) -> str:
    """The reply with every unsupported write claim replaced by the honest line (once). Unchanged when off / no claims."""
    m = mode()
    if m == "off" or not (reply or "").strip():
        return reply
    try:
        out, replaced = [], False
        for sentence in split_sentences(reply):
            claim = find_claim(sentence)
            if claim is None or supported(claim, user_id, started, user_text=user_text, tools_run=tools_run):
                out.append(sentence)
                continue
            _log_blocked(claim, user_id, lane, "shadow" if m == "shadow" else "replaced")
            if m == "shadow":
                out.append(sentence)
            elif not replaced and claim.honest:
                replaced = True
                out.append(claim.honest + sentence[len(sentence.rstrip()):])
        return "".join(out) if out else reply
    except Exception as exc:  # noqa: BLE001 - never break a turn over bookkeeping
        logger.warning("write_claim_guard.guard_text failed (reply passed through): %s", exc)
        return reply


async def guard_stream(inner: AsyncIterator[str], user_id: Optional[str], started: float, *, user_text: str = "",
                       lane: str = "brain") -> AsyncIterator[str]:
    """Pass ``inner`` through, replacing an unsupported write-claim sentence. Sentinels pass untouched (and tell the guard
    which tools ran); text is held back only while a sentence could still become a claim."""
    m = mode()
    if m == "off":
        async for delta in inner:
            yield delta
        return
    tools_run: set = set()
    held: list = []     # the deltas not yet released, as they arrived (an unchanged sentence goes out in the SAME pieces)
    head = ""           # the part of the CURRENT sentence already released (so a claim is judged on all of it)
    replaced = False

    def judge(sentence: str) -> Optional[str]:
        """None = release the sentence untouched; a string = release that instead (may be empty)."""
        nonlocal replaced, head
        whole = head + sentence
        head = ""
        claim = find_claim(whole)
        if claim is None or supported(claim, user_id, started, user_text=user_text, tools_run=frozenset(tools_run)):
            return None
        _log_blocked(claim, user_id, lane, "shadow" if m == "shadow" else "replaced")
        if m == "shadow":
            return None
        trailing = sentence[len(sentence.rstrip()):]
        if replaced or not claim.honest:
            return trailing
        replaced = True
        return claim.honest + trailing

    cleared = 0         # leading characters of ``held`` already judged clean (released once a whole piece is clean)

    try:
        async for delta in inner:
            if delta.startswith(_SENTINEL_PREFIXES):
                if delta.startswith("__TOOL__:"):
                    name = _tool_name(delta)
                    if name:
                        tools_run.add(name)
                yield delta
                continue
            held.append(delta)
            while True:
                buf = "".join(held)
                end = _sentence_end(buf, cleared)
                if end is None:
                    break
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
            if tail and not _could_become_claim(tail):
                head += tail
                for piece in held:
                    yield piece
                held.clear()
                cleared = 0
        buf = "".join(held)
        if buf:
            verdict = judge(buf[cleared:]) if cleared < len(buf) else None
            if verdict is None:
                for piece in held:
                    yield piece
            else:
                yield buf[:cleared] + verdict
    finally:
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass
