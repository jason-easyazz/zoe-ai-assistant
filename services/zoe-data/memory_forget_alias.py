"""The forget-alias sweep (MemPalace take 1; bench HM-F5): forgetting a name also OFFERS its misspellings - and never forgets one unasked.

"Forget Marisol" erases what names her and writes her hashed name to the permanent ledger (#1883). A transcript spells her "Marisal",
"Marysol", "Mari sol": none hashes to the ledger's key or matches the whole-word pattern, so they survived and were mined again.

* RULE (0 false matches on a 73,604-word dictionary at these bounds): a candidate is a WHOLE token of the owner's text within
  ``max_edits(letters)`` edits of the name - 7+ letters 2, 5-6 letters 1, 4 or fewer none ("Dan" is not "Dana"). Never a substring. A SPLIT
  spelling (2-3 adjacent tokens, same first letter, no 1-letter fragment) joins to within one edit LESS: "Mari sol" / "Mari-sol" match
  "Marisol", "Mar is sold" does not. A run containing the exact name is the exact forget's business.
* WHERE: the owner's rows in EVERY status and live people rows. Never another user's.
* WHAT: each candidate is ONE ``pending_suggestions`` question (``forget_alias``, the ``memory_dispute`` shape of #1868). The forget reply
  asks the first aloud, every session shows a Yes/No card, a bare yes/no binds to the question just asked. YES runs the SAME permanent path
  as the original forget (``memory_forget_entity``: tombstone, ledger, archive, physical erase, cascade), so every transcript reader skips
  it. NO resolves it and counts ``forget_alias|owner_declined`` in the reject ledger. Unanswered, the alias stays exactly as it is.
* PRIVACY: a question row holds the candidate, never the forgotten name, and is scrubbed once answered. Counts only are logged.

Flag ``ZOE_FORGET_ALIAS_SWEEP``: ``on`` (default) | ``shadow`` (find and count, never ask) | ``off``. Best-effort: never raises into a forget.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import forget_match as fm

logger = logging.getLogger(__name__)

ENV = "ZOE_FORGET_ALIAS_SWEEP"
ACTION = "forget_alias"
SESSION = "forget-alias"        # not tied to a conversation: the questions surface on every session until answered
MAX_QUESTIONS = 3               # per forget; the rest are counted as ``over_cap`` in the reject ledger, not asked
MAX_RUN = 3                     # a split spelling is at most this many adjacent tokens
STATUSES = ("approved", "pending", "disputed", "superseded", "archived", "rejected")
ASKED_TTL_S = 600.0             # how long an asked-aloud question stays bindable to a bare yes / no
INSTRUCTION = " Say yes to forget it too, or no to leave it."

_WORD_RE = re.compile(r"\w+", re.UNICODE)


# ── the flag ─────────────────────────────────────────────────────────────────

def mode() -> str:
    """``on`` (default) | ``shadow`` | ``off``. An unrecognised value keeps the default (a privacy question, not a risk)."""
    raw = (os.environ.get("ZOE_FORGET_ALIAS_SWEEP") or "").strip().lower()
    return "off" if raw in ("off", "0", "false", "no", "disabled") else ("shadow" if raw == "shadow" else "on")


# ── the rule ─────────────────────────────────────────────────────────────────

def max_edits(letters: "int | str") -> int:
    """7+ codepoints -> 2 edits, 5-6 -> 1, 4 or fewer -> 0 (none: a 2-edit radius round a 3-4 letter name holds 41-363 ordinary words);
    a script written without spaces -> 1 from 3 codepoints (``forget_match``). Pass the joined name for the script rule; an int is Latin."""
    if isinstance(letters, str):
        return fm.max_edits(letters)
    return 2 if letters >= 7 else (1 if letters >= 5 else 0)


def edit_distance(a: str, b: str, limit: Optional[int] = None) -> int:
    """Levenshtein distance (MemPalace's ``fact_checker._edit_distance``); with ``limit``, ``limit + 1`` once it cannot be within it."""
    if a == b:
        return 0
    if limit is not None and abs(len(a) - len(b)) > limit:
        return limit + 1
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if limit is not None and min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _words(text: str) -> "list[str]":
    return _WORD_RE.findall(unicodedata.normalize("NFKC", str(text or "")))


def _fold(w: str) -> str:
    return fm.fold(w)


@dataclass
class Alias:
    """One candidate spelling: first written form, normalised key (what the ledger would hash), and where it was found."""
    display: str
    key: str
    memories: int = 0
    contacts: int = 0


@dataclass
class _Probe:
    tokens: "list[str]"
    joined: str
    budget_single: int
    budget_split: int

    @classmethod
    def of(cls, name: str) -> "Optional[_Probe]":
        toks = [_fold(w) for w in _words(name)][:6]
        joined = "".join(toks)
        k = max_edits(joined)
        if k == 0 or not toks or not joined.isalpha():
            return None
        return cls(toks, joined, k, max(k - 1, 0))


def _contains_run(tokens: "list[str]", run: "list[str]") -> bool:
    n = len(run)
    return any(tokens[i:i + n] == run for i in range(len(tokens) - n + 1))


def find_in_text(name: str, text: str) -> "list[tuple[str, str]]":
    """The ``(display, key)`` of every alias candidate of ``name`` in ``text`` (whole tokens, never substrings)."""
    probe = _Probe.of(name)
    if probe is None:
        return []
    words = _words(text)
    low = [_fold(w) for w in words]
    out: "dict[str, str]" = {}
    for t in fm.tokens(text):               # a name inside a run of a script written without spaces: windows of the run
        if t.uns and len(t.text) == t.end - t.start:
            for a in range(len(t.text)):
                for ln in range(max(2, len(probe.joined) - probe.budget_single), len(probe.joined) + probe.budget_single + 1):
                    w = t.text[a:a + ln]
                    if len(w) == ln and w != probe.joined and edit_distance(w, probe.joined, probe.budget_single) <= probe.budget_single:
                        out.setdefault(w, text[t.start + a:t.start + a + ln])
    longest = min(MAX_RUN, max(len(probe.tokens) + 2, 2))
    for i in range(len(words)):
        for n in range(1, longest + 1):
            if i + n > len(words):
                break
            run = low[i:i + n]
            if not all(t.isalpha() for t in run):
                continue
            # a run that CONTAINS the exact name is the exact forget's business, not an alias ("Marisol's")
            if _contains_run(run, probe.tokens) or run == probe.tokens:
                continue
            joined = "".join(run)
            if n == 1:
                budget = probe.budget_single
            else:
                if joined[0] != probe.joined[0] or any(len(t) < 2 for t in run):
                    continue
                budget = probe.budget_split
            if edit_distance(joined, probe.joined, budget) <= budget:
                key = " ".join(run)
                out.setdefault(key, " ".join(words[i:i + n]))
    return [(disp, key) for key, disp in out.items()]


def find_aliases(name: str, texts: "Iterable[tuple[str, str]]") -> "list[Alias]":
    """Alias candidates of ``name`` over ``texts`` = ``(source, text)`` with source ``memory`` or ``contact``,
    most-found first. Pure: no I/O."""
    found: "dict[str, Alias]" = {}
    for source, text in texts:
        for disp, key in find_in_text(name, text):
            a = found.setdefault(key, Alias(display=disp, key=key))
            if source == "contact":
                a.contacts += 1
            else:
                a.memories += 1
    return sorted(found.values(), key=lambda a: (-(a.memories + a.contacts), a.key))


# ── what is scanned ──────────────────────────────────────────────────────────

async def collect_texts(user_id: str, svc: Any) -> "list[tuple[str, str]]":
    """The owner's text: every row in every status (the forget sweep's ownership guard: another user's row is never read) + live people rows."""
    out: "list[tuple[str, str]]" = []
    for status in STATUSES:
        offset = 0
        while True:
            try:
                page = await svc.list_by_status(user_id=user_id, status=status, limit=1000, offset=offset)
            except Exception as exc:  # noqa: BLE001 - a status that cannot be listed is skipped, the rest are scanned
                logger.info("forget_alias: could not list %s rows (%s)", status, type(exc).__name__)
                break
            if not page:
                break
            for r in page:
                md = getattr(r, "metadata", {}) or {}
                if md.get("user_id") == user_id or md.get("wing") == user_id:
                    out.append(("memory", getattr(r, "text", "") or ""))
            if len(page) < 1000:
                break
            offset += len(page)
    try:
        from db_pool import get_db_ctx  # type: ignore[import]

        async with get_db_ctx() as db:
            cur = await db.execute(
                "SELECT name FROM people WHERE user_id = ? AND (deleted = 0 OR deleted IS NULL)", (user_id,))
            out.extend(("contact", str(r[0] or "")) for r in await cur.fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.info("forget_alias: people graph not scanned (%s)", type(exc).__name__)
    return out


# ── the question ─────────────────────────────────────────────────────────────

def _where(a: Alias) -> str:
    if a.memories and a.contacts:
        return f"{a.memories} of your memories and in your contacts"
    if a.contacts:
        return "your contacts"
    return f"{a.memories} of your memories" if a.memories != 1 else "one of your memories"


def question_text(a: Alias) -> str:
    if not (a.memories or a.contacts):
        return f'Did you also mean "{a.display}"? I came across it just now.'
    return f'Did you also mean "{a.display}"? I found it in {_where(a)}.'


def question_with_instruction(question: str) -> str:
    return question + INSTRUCTION


# ── the in-process "asked aloud" registry (a bare yes/no binds only to what was just asked) ──────

# {user_id: (asked_at_monotonic, suggestion_id, question)}
_asked: "dict[str, tuple[float, str, str]]" = {}


def peek_asked(user_id: str) -> "Optional[dict[str, str]]":
    rec = _asked.get(user_id)
    if rec is None or time.monotonic() - rec[0] > ASKED_TTL_S:
        _asked.pop(user_id, None)
        return None
    return {"id": rec[1], "question": rec[2]}


def clear_asked(user_id: str) -> None:
    _asked.pop(user_id, None)


def reset_state() -> None:
    _asked.clear()


# ── the offer ────────────────────────────────────────────────────────────────

async def _open_questions(user_id: str) -> "list[dict[str, Any]]":
    """The owner's unanswered alias questions, oldest first."""
    import pending_suggestions as ps

    async with ps.get_db_ctx() as db:
        rows = await db.fetch(
            """SELECT id, offer_phrase, pre_filled_slots FROM pending_suggestions
               WHERE user_id = $1 AND action_type = $2 AND resolved = 0 ORDER BY created_at ASC""",
            user_id, ACTION)
    return [{"id": r["id"], "question": r["offer_phrase"] or "", "alias": json.loads(r["pre_filled_slots"] or "{}").get("alias") or ""}
            for r in rows]


async def next_question(user_id: str) -> str:
    """The sentence asking the oldest unanswered alias question aloud ('' when none), remembered so a bare yes/no binds to it."""
    try:
        qs = await _open_questions(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("forget_alias: open questions unavailable (%s)", type(exc).__name__)
        return ""
    if not qs:
        clear_asked(user_id)
        return ""
    q = qs[0]
    _asked[user_id] = (time.monotonic(), q["id"], q["question"])
    return " " + question_with_instruction(q["question"])


async def offer(user_id: str, name: str, svc: Any) -> str:
    """After the exact forget of ``name``: store ONE question per alias candidate (at most ``MAX_QUESTIONS``) and return the sentence asking
    the first aloud ('' when none, or off / shadow). Never raises, never forgets anything."""
    m = mode()
    if m == "off" or not user_id or not (name or "").strip():
        return ""
    try:
        from memory_reject_ledger import record_reject

        texts = await collect_texts(user_id, svc)
        aliases = await asyncio.to_thread(find_aliases, name, texts)   # pure CPU over the whole store: off the event loop
        if not aliases:
            return ""
        if m == "shadow":
            logger.info("FORGET_ALIAS_SHADOW user=%s candidates=%d (nothing asked)", user_id, len(aliases))
            return ""
        import memory_forgotten
        import pending_suggestions as ps

        asked = {str(q["alias"]).casefold() for q in await _open_questions(user_id)}
        fresh = []
        for a in aliases:
            if a.display.casefold() in asked or await memory_forgotten.matches(user_id, a.display):
                continue                    # already asked, or already forgotten: never a second question
            fresh.append(a)
        for a in fresh[MAX_QUESTIONS:]:
            record_reject("forget_alias", "over_cap", gate=False)
        stored = 0
        for a in fresh[:MAX_QUESTIONS]:
            q = question_text(a)
            stored += await ps.store_suggestions(user_id, SESSION, [{
                "action_type": ACTION, "description": q[:500], "offer_phrase": q[:300],
                "pre_filled_slots": {"alias": a.display, "memories": a.memories, "contacts": a.contacts},
            }])
        logger.info("FORGET_ALIAS_OFFER user=%s candidates=%d asked=%d", user_id, len(aliases), stored)
        return await next_question(user_id) if stored else ""
    except Exception as exc:  # noqa: BLE001 - the forget itself already happened
        logger.warning("forget_alias: sweep failed (%s) - nothing asked", type(exc).__name__)
        return ""


MAX_OPEN = 6    # unanswered near-spelling questions at once; the rest wait for the next sighting


async def queue_spellings(user_id: str, spellings: "Iterable[str]") -> int:
    """A write was held out because it names a NEAR spelling of a forgotten name (``memory_forgotten`` near guard): ask the owner
    about each (once; not while a question is open; not past ``MAX_OPEN``). Returns the questions stored. Never raises."""
    if mode() != "on" or not user_id:
        return 0
    try:
        import pending_suggestions as ps

        open_qs = await _open_questions(user_id)
        asked = {str(q["alias"]).casefold() for q in open_qs}
        room = MAX_OPEN - len(open_qs)
        stored = 0
        for sp in dict.fromkeys(str(x).strip() for x in spellings if str(x).strip()):
            if room <= 0 or sp.casefold() in asked:
                continue
            q = question_text(Alias(display=sp, key=fm.joined_key(sp)))
            stored += await ps.store_suggestions(user_id, SESSION, [{
                "action_type": ACTION, "description": q[:500], "offer_phrase": q[:300],
                "pre_filled_slots": {"alias": sp, "memories": 0, "contacts": 0}}])
            asked.add(sp.casefold())
            room -= 1
        if stored:
            logger.info("FORGET_NEAR_ASK user=%s asked=%d", user_id, stored)
        return stored
    except Exception as exc:  # noqa: BLE001
        logger.debug("forget_alias: near-spelling question not stored (%s)", type(exc).__name__)
        return 0


# ── the answer ───────────────────────────────────────────────────────────────

async def forget_confirmed(user_id: str, alias: str) -> bool:
    """YES: forget ``alias`` through the SAME permanent path as the original forget. True when it landed (the ledger answers for it)."""
    alias = (alias or "").strip()
    if not alias:
        return False
    import intent_router
    import memory_forgotten

    await intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": alias, "alias_confirmed": True}), user_id)
    if memory_forgotten.configured() and not await memory_forgotten.matches(user_id, alias):
        logger.warning("forget_alias: the confirmed alias is not in the ledger for user=%s", user_id)
        return False
    return True


async def answer(user_id: str, suggestion_id: str, accept: bool) -> str:
    """A spoken or typed yes/no through the panel's own path (``execute_suggestion`` / ``mark_resolved``); asks the next question if one waits."""
    import pending_suggestions as ps

    clear_asked(user_id)
    if accept:
        res = await ps.execute_suggestion(suggestion_id, user_id)
        if res.get("ok"):
            head = "Done - I've forgotten that spelling too."
        else:
            logger.info("forget_alias: confirm failed user=%s err=%s", user_id, res.get("error"))
            head = "I couldn't forget that just now, so nothing was changed. Ask me again and I'll retry."
    else:
        head = "Okay, I'll leave it." if await ps.mark_resolved(suggestion_id, user_id) else \
            "I couldn't update that just now, so I may ask again."
    return head + await next_question(user_id)


async def match_reply(user_id: str, text: str, previous_assistant: Any) -> "Optional[tuple[str, bool]]":
    """``(suggestion_id, accept)`` when a SHORT yes/no answers the alias question Zoe JUST asked (asked aloud within ``ASKED_TTL_S`` and the
    previous assistant message ends with it); None otherwise (normal routing). ``previous_assistant`` = ``async (user_id) -> Optional[str]``."""
    if mode() != "on":
        return None
    import contacts_conversation as cc
    import intent_router

    kind = intent_router._offer_reply_kind(text, "")      # the offer-reply shape rule: short, built only from yes/no words
    rec = peek_asked(user_id) if kind else None
    if rec is None or not cc.asked_in_message(await previous_assistant(user_id), rec["question"]):
        return None
    return rec["id"], kind == "accept"
