"""Single canonical list writer.

One INSERT site each for the `lists` and `list_items` tables, shared by every
Wave-3 writer (the voice/direct executor in ``intent_router``, the
``list_add_item`` MCP tool, and the ``/api/lists`` router). Callers keep their
own feature gating, notification channel (``_notify_ui`` vs the push
broadcaster), response formatting, and commit/re-read — those DIFFER per caller
and preserving them is how observable behaviour stays identical. This module
owns ONLY the row writes plus the shared resolve-or-create mechanics.

Both schemas (see alembic 0001_initial_schema.py) are written as the full
superset of the trio's columns; voice-path callers that only supply a subset
get the same NULLs / defaults their narrower INSERTs produced (``priority``
defaults to the DB's own ``'normal'``).

Follow-up (out of scope here): skybridge_service.py and pending_suggestions.py
still carry their own list INSERTs.
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

import write_receipts

# The voice/MCP rule for a freshly auto-created list's visibility. Callers pass
# the result explicitly so the choice stays visible at the call site.
PERSONAL_LIST_TYPES = frozenset({"personal", "tasks", "shopping"})


def default_visibility(list_type: str) -> str:
    """Visibility the voice/MCP add paths give a list they auto-create."""
    return "personal" if list_type in PERSONAL_LIST_TYPES else "family"


# ── Item text hygiene (live 2026-10-12: "Can you add noodles? Noodles. To the shopping list." stored "noodles? Noodles";
# a whole sentence the user said to Zoe was stored as an item). One cleaner and one plausibility test, used by BOTH the
# Skybridge parser and the intent_router parser so a spoken item reads the same on every lane.
_FILLER_WORDS = frozenset({"um", "uh", "er", "erm", "ah", "hmm", "mm", "uhh", "umm", "eh"})
_LEAD_WORDS = frozenset({"please", "pls", "and", "also", "then", "just", "so", "okay", "ok", "like", "well"})
_EDGE_PUNCT_RE = re.compile(r"^[\s.,;:!?\-]+|[\s.,;:!?\-]+$")
# A sentence is not an item: it opens with a pronoun / auxiliary / wh-word / discourse marker ("I don't know where you got
# that from"). Task verbs ("do laundry", "call mum") are deliberately NOT here.
_NON_ITEM_LEADS = frozenset({
    "i", "i'm", "im", "i've", "i'd", "i'll", "you", "you're", "we", "we're", "they", "he", "she", "it", "it's", "that",
    "that's", "this", "what", "what's", "why", "how", "who", "when", "where", "which", "is", "are", "was", "were", "am",
    "don't", "dont", "didn't", "doesn't", "isn't", "can't", "cant", "won't", "wouldn't", "couldn't", "if", "because",
    "but", "actually", "no", "yes", "yeah", "nope", "oh", "there", "here", "let's", "lets",
})
# A possessive opener ("my passport", "your book", "our spare keys") starts a perfectly good item; it starts a SENTENCE only when a
# verb-ish word follows ("my phone is dead", "your dog doesn't like it"). Review (#2004): rejecting every "my …" made "add my
# passport to my shopping list" fall into the garbled matcher and store "dd my passport".
_POSSESSIVE_LEADS = frozenset({"my", "your", "our"})
_SENTENCE_WORDS = frozenset({
    "is", "are", "was", "were", "am", "be", "been", "has", "have", "had", "will", "would", "could", "should", "can", "cannot",
    "do", "does", "did", "don't", "dont", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't", "can't", "cant",
    "won't", "wouldn't", "couldn't", "shouldn't", "i", "i'm", "i've", "i'll", "i'd", "you", "you're", "you've", "we", "they",
    "he", "she", "it", "it's", "that's", "because",
})
MAX_ITEM_WORDS = 12
# Nouns that name no item at all ("add things to the list") - never stored.
_VAGUE_ITEMS = frozenset({"things", "thing", "stuff", "something", "it", "that", "them", "this", "these", "those", "one",
                          "anything", "everything", "some", "items", "item"})


# A period that ends an ABBREVIATION is part of the item, not a sentence break: "Dr. Pepper", "Mr. Clean", "U.S. stamps", "J. Crew".
_BREAK_RE = re.compile(r"[.!?]+(?=\s|$)")
_ABBREV_END_RE = re.compile(
    r"(?:^|\s)(?:mr|mrs|ms|mx|dr|st|jr|sr|prof|mt|vs|inc|ltd|co|ave|rd|blvd|oz|lb|lbs|tbsp|tsp|pkg|approx|etc|[a-z])$"
    r"|\b[a-z](?:\.[a-z])+$", re.IGNORECASE)


def _real_breaks(text: str) -> list:
    """``(start, end)`` of each terminator that really ends a sentence in ``text`` - a lone abbreviation period is not one."""
    out = []
    for m in _BREAK_RE.finditer(text):
        if m.group() == "." and _ABBREV_END_RE.search(text[:m.start()]) and m.end() < len(text):
            continue
        out.append((m.start(), m.end()))
    return out


def _split_sentences(text: str) -> list:
    segments, prev = [], 0
    for start, end in _real_breaks(text):
        segments.append(text[prev:start])
        prev = end
    segments.append(text[prev:])
    return [seg.strip() for seg in segments if seg.strip()]


def clean_item_text(raw: str) -> str:
    """The item a person meant, from the words STT handed over: filler words dropped, a stutter ("noodles? Noodles",
    "and and") collapsed, edge punctuation stripped. Casing is kept. Never raises; "" when nothing is left OR when the words hold
    two different sentences (a spoken correction is ambiguous, so it is declined, never glued into one item)."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return ""
    # Segments split on sentence punctuation: a repeated segment ("noodles? Noodles.") is a stutter, kept once.
    segments = _split_sentences(text)
    kept: list[str] = []
    seen: set[str] = set()
    for seg in segments:
        words = [w for w in seg.split() if w.lower().strip(",;:") not in _FILLER_WORDS]
        while words and words[0].lower().strip(",;:") in _LEAD_WORDS and (kept or len(words) > 1):
            words = words[1:]
        # adjacent repeated words ("and and", "milk milk")
        deduped: list[str] = []
        for w in words:
            if deduped and w.lower().strip(",;:") == deduped[-1].lower().strip(",;:"):
                continue
            deduped.append(w)
        seg_clean = _EDGE_PUNCT_RE.sub("", " ".join(deduped))
        key = seg_clean.lower()
        if seg_clean and key not in seen:
            seen.add(key)
            kept.append(seg_clean)
    if len(kept) > 1:
        # Two DIFFERENT sentences are not one item: "add milk? Actually no, add eggs" is a correction, and gluing them gave the
        # item "milk, Actually no, add eggs". Only a repetition collapses (above); anything else is declined as ambiguous.
        return ""
    return _EDGE_PUNCT_RE.sub("", "".join(kept))


# Auxiliaries that are also common nouns: "my can opener", "our can of paint", "my spare can", "your last will".
_NOUN_HOMOGRAPHS = frozenset({"can", "will"})
# Words that, straight AFTER one of those auxiliaries, make it the noun of a compound even mid-phrase: "my tin can OPENER", "my
# brother's can OPENER", "our old can OF PAINT", "my last will AND TESTAMENT". Data, not code: a new compound is a new row here.
# A tail is the compound's tokens AFTER the auxiliary; ``_ANY_NOUN`` is a slot for "a noun follows" ("can of <noun>"). A single tail
# word is only listed when it cannot be a verb or a preposition on its own: "of" and "label" alone are NOT ("my printer can label
# packages", "my printer can of course print" are sentences), so they need a second token. "my phone can ring" / "my phone will
# not work" / "my phone will and does ring" match none of them and stay sentences.
_ANY_NOUN = "*"
_NOUN_COMPOUNDS = {
    "can": (("opener",), ("openers",), ("of", _ANY_NOUN), ("lid",), ("lids",), ("crusher",), ("tab",), ("label", "maker"),
            ("label", "makers")),
    "will": (("and", "testament"), ("testament",)),
}
# Function words that cannot be the noun of "can of <noun>" ("my printer can of course print").
_NOT_A_NOUN = frozenset({"course", "it", "them", "that", "this", "these", "those", "which", "what", "all", "any", "us", "me", "you",
                         "him", "her", "and", "or", "but", "to", "the", "a", "an", "not"})


def _tail_matches(tail: tuple, ahead: list) -> bool:
    if len(ahead) < len(tail):
        return False
    return all((a not in _NOT_A_NOUN) if t == _ANY_NOUN else a == t for t, a in zip(tail, ahead))


def _is_noun_compound(word: str, following: list) -> bool:
    """True when the auxiliary-looking ``word`` is followed by the rest of a known noun compound ("can" + "opener")."""
    ahead = [w.lower().strip(",;:") for w in following]
    return any(_tail_matches(tail, ahead) for tail in _NOUN_COMPOUNDS.get(word, ()))


def _has_predicate(words: list) -> bool:
    """True when the words after a possessive hold a predicate - the sentence-making auxiliary / pronoun of "my phone IS dead". The
    word straight after the possessive is the head noun and never a predicate ("my CAN opener"); an auxiliary-looking noun that
    ENDS the phrase is a noun too ("my spare CAN"), and so is one that opens a known noun compound mid-phrase ("my tin CAN opener",
    "my last WILL and testament"). Anything else in ``_SENTENCE_WORDS`` makes it a sentence."""
    for n, w in enumerate(words):
        if n == 0:
            continue
        w = w.lower().strip(",;:")
        if w in _SENTENCE_WORDS and not (w in _NOUN_HOMOGRAPHS and (n == len(words) - 1 or _is_noun_compound(w, words[n + 1:]))):
            return True
    return False


def is_plausible_item(item: str) -> bool:
    """False for text that is a SENTENCE rather than something to put on a list - so a statement the user made to Zoe is never
    stored as an item (and never answered with "Added …"). Pure; takes the already-cleaned item."""
    text = (item or "").strip()
    if not text:
        return False
    words = text.split()
    if len(words) > MAX_ITEM_WORDS:
        return False
    if _real_breaks(text):                       # a full stop / ? / ! INSIDE the item: more than one sentence
        return False
    if text.lower() in _VAGUE_ITEMS:
        return False
    lead = words[0].lower().strip(",;:")
    if lead in _POSSESSIVE_LEADS:
        return len(words) > 1 and not _has_predicate(words[1:])
    return lead not in _NON_ITEM_LEADS


async def create_list_record(
    db,
    *,
    user_id: str,
    name: str,
    list_type: str,
    visibility: str,
    description: Optional[str] = None,
) -> dict:
    """Insert one row into ``lists`` and return a record dict.

    Takes an already-open ``db`` handle (AsyncpgCompat / aiosqlite style) and
    issues the single canonical INSERT with ``?`` placeholders. Does NOT gate
    access, notify the UI, or commit (asyncpg auto-commits; ``db.commit()`` is
    a no-op) — those are the caller's job. Callers that re-read the row for
    their response may ignore the returned dict.
    """
    list_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO lists (id, user_id, name, list_type, description, visibility)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (list_id, user_id, name, list_type, description, visibility),
    )
    return {
        "id": list_id,
        "user_id": user_id,
        "name": name,
        "list_type": list_type,
        "description": description,
        "visibility": visibility,
    }


async def create_item_record(
    db,
    *,
    list_id: str,
    text: str,
    priority: str = "normal",
    category: Optional[str] = None,
    quantity: Optional[str] = None,
    parent_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
) -> dict:
    """Insert one row into ``list_items`` and return a record dict.

    Same contract as :func:`create_list_record`: row write only, no access
    checks, no notify, no commit. ``priority`` defaults to ``'normal'`` — the
    DB default the narrower voice/MCP INSERTs relied on by omitting the column.
    """
    item_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO list_items (id, list_id, text, priority, category, quantity, parent_id, assigned_to)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (item_id, list_id, text, priority, category, quantity, parent_id, assigned_to),
    )
    return {
        "id": item_id,
        "list_id": list_id,
        "text": text,
        "priority": priority,
        "category": category,
        "quantity": quantity,
        "parent_id": parent_id,
        "assigned_to": assigned_to,
    }


async def find_list_id(db, *, user_id: str, list_type: str, name: str) -> Optional[str]:
    """Resolve a list by type + exact name, family lists first.

    The shared voice/MCP resolution: the user's own lists and family-visible
    lists both match; a family list wins a name tie so household adds converge
    on the shared list.
    """
    cursor = await db.execute(
        "SELECT id FROM lists WHERE list_type=? AND name=? AND deleted=0"
        " AND (user_id=? OR visibility='family')"
        " ORDER BY CASE WHEN visibility='family' THEN 0 ELSE 1 END LIMIT 1",
        (list_type, name, user_id),
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def _find_recent_duplicate(db, *, list_id: str, text: str, window_s: int) -> Optional[str]:
    # created_at is a TEXT column, so it must be cast before the timestamp
    # comparison — a bare `created_at > now() - interval` throws `operator does
    # not exist: text > timestamp` on Postgres, which silently dropped the whole
    # direct add to the mcporter fallback (live 2026-07-08: 69 such errors).
    cursor = await db.execute(
        "SELECT id FROM list_items WHERE list_id=? AND lower(text)=lower(?)"
        f" AND deleted=0 AND created_at::timestamptz > now() - interval '{int(window_s)} seconds' LIMIT 1",
        (list_id, text),
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def add_item_to_list(
    db,
    *,
    user_id: str,
    list_type: str,
    list_name: str,
    text: str,
    quantity: Optional[str] = None,
    category: Optional[str] = None,
    new_list_visibility: str,
    dedup_window_s: int = 0,
) -> dict:
    """Resolve-or-create a list and add one item — the shared voice/MCP add.

    Behaviour choices stay with the caller: ``new_list_visibility`` sets the
    auto-created list's visibility (pass :func:`default_visibility`'s result to
    keep the current rule), ``dedup_window_s`` > 0 treats an identical
    (case-insensitive) item added within that window as already done — the
    voice retry-idempotency guard; 0 disables it. Notification is NOT sent
    here.

    When the list does not exist, the list row and its first item land in ONE
    transaction (asyncpg auto-commits each statement, so without it a failed
    item insert orphans an empty list — the MCP-path bug this module fixes).

    Returns ``{"list_id", "item_id", "created_list", "deduped"}``; on a dedup
    hit ``item_id`` is the existing duplicate row's id and nothing is written.
    """
    list_id = await find_list_id(db, user_id=user_id, list_type=list_type, name=list_name)
    if list_id is not None:
        if dedup_window_s > 0:
            dup_id = await _find_recent_duplicate(
                db, list_id=list_id, text=text, window_s=dedup_window_s
            )
            if dup_id is not None:
                # the item IS on the list (a replayed add), so a reply that says so is true
                write_receipts.record(user_id, "list", dup_id, "add_item_to_list:dedup")
                return {"list_id": list_id, "item_id": dup_id, "created_list": False, "deduped": True}
        # Existing list: a single INSERT is already atomic.
        item = await create_item_record(
            db, list_id=list_id, text=text, quantity=quantity, category=category
        )
        write_receipts.record(user_id, "list", item["id"], "add_item_to_list")
        return {"list_id": list_id, "item_id": item["id"], "created_list": False, "deduped": False}

    async def _write_new_list_and_item() -> dict:
        new_list = await create_list_record(
            db,
            user_id=user_id,
            name=list_name,
            list_type=list_type,
            visibility=new_list_visibility,
        )
        item = await create_item_record(
            db, list_id=new_list["id"], text=text, quantity=quantity, category=category
        )
        return {
            "list_id": new_list["id"],
            "item_id": item["id"],
            "created_list": True,
            "deduped": False,
        }

    txn = getattr(db, "transaction", None)
    if callable(txn):
        async with txn():
            outcome = await _write_new_list_and_item()
    else:
        # Fallback (e.g. a DB shim without transaction support).
        outcome = await _write_new_list_and_item()
    write_receipts.record(user_id, "list", outcome["item_id"], "add_item_to_list:new_list")
    return outcome
