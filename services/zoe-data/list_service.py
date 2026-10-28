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

import panel_principal
import write_receipts

# The voice/MCP rule for a freshly auto-created list's visibility. Callers pass
# the result explicitly so the choice stays visible at the call site.
# ``shopping`` is NOT here: the household has ONE shopping list (a fridge list), so an auto-created one is family-visible.
# Auto-creating it as the first speaker's PERSONAL list made the next person's "add milk" find nothing and create a second
# list - the duplicate shopping lists seen on the panel (2026-10-15).
PERSONAL_LIST_TYPES = frozenset({"personal", "tasks"})
# The only list a panel guest may bring into existence; every other household list must already exist.
GUEST_CREATABLE_LIST_TYPES = frozenset({"shopping"})


class HouseholdListUnavailable(Exception):
    """A guest asked for a household list that does not exist and may not be created by a guest."""


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


_QM_RE = re.compile(r"[?!]+")


def normalize_item_text(raw: str) -> str:
    """The text a list item is STORED (and compared) as: STT question / exclamation marks stripped from anywhere in it, and an
    immediate case-insensitive repeat of the whole phrase collapsed ("noodles? Noodles" -> "noodles", "eggs eggs" -> "eggs").
    Casing of the first copy is kept. Pure; never raises; a value with nothing left comes back as the stripped original."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", _QM_RE.sub(" ", text)).strip(" ,;:")
    words = flat.split()
    for size in range(1, len(words) // 2 + 1):
        if len(words) % size == 0:
            chunk = [w.lower().strip(",;:.") for w in words[:size]]
            if all([w.lower().strip(",;:.") for w in words[i:i + size]] == chunk for i in range(0, len(words), size)):
                words = words[:size]
                break
    return " ".join(words).strip(" ,;:") or text


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "sses", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def item_key(text: str) -> str:
    """Identity of a list item for "is it already there?" and remove matching: normalised, case-, punctuation- and
    plural-insensitive ("Eggs" == "egg"; "noodles? Noodles" == "noodles")."""
    flat = re.sub(r"[^\w\s']", " ", normalize_item_text(text).lower())
    return " ".join(_singular(w) for w in flat.split())


def find_matching_item(rows, text: str):
    """The first of ``rows`` (mappings with a ``text``) that is the same item as ``text`` by :func:`item_key`, else None."""
    want = item_key(text)
    if not want:
        return None
    for row in rows or []:
        if item_key(str(dict(row).get("text") or "")) == want:
            return row
    return None


#: "and" inside these is part of ONE item, never a list of two ("add salt and pepper" adds one thing)
_COMPOUND_ITEMS = frozenset({
    "salt and pepper", "mac and cheese", "macaroni and cheese", "fish and chips", "bread and butter", "rice and beans",
    "peanut butter and jelly", "surf and turf", "pots and pans", "tea and coffee", "cheese and crackers", "milk and honey",
    "chips and dip", "oil and vinegar", "bacon and eggs", "ham and cheese", "salt and vinegar", "sweet and sour",
})


def split_items(text: str) -> list:
    """The separate items in one spoken add ("eggs and milk", "eggs, milk and bread") - commas and " and " split; a known
    compound ("salt and pepper") stays one. One item comes back as a one-element list; nothing as []."""
    flat = normalize_item_text(text)
    if not flat:
        return []
    if flat.lower() in _COMPOUND_ITEMS:
        return [flat]
    out = []
    for part in re.split(r",\s*(?:and\s+)?|\s+and\s+", flat, flags=re.IGNORECASE):
        part = normalize_item_text(part)
        if part and part.lower() not in {"and", "also", "some"}:
            out.append(part)
    return out or [flat]


def _join(names: list) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def add_reply(added: list, existing: list, where: str) -> str:
    """The one-sentence answer to an add. ``where`` is the spoken place ("the shopping list"). Nothing new: "Milk's already on
    the shopping list."; some of each: "Added eggs to the shopping list, and milk's already on it."."""
    added = [normalize_item_text(x) for x in added if normalize_item_text(x)]
    existing = [normalize_item_text(x) for x in existing if normalize_item_text(x)]
    if added and not existing:
        return f"Added {_join(added)} to {where}."
    if not existing:
        return ""
    if not added:
        names = [existing[0][:1].upper() + existing[0][1:]] + existing[1:]
        return f"{names[0]}'s already on {where}." if len(names) == 1 else f"{_join(names)} are already on {where}."
    tail = f"{existing[0]}'s already on it" if len(existing) == 1 else f"{_join(existing)} are already on it"
    return f"Added {_join(added)} to {where}, and {tail}."


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
    text = normalize_item_text(text) or text
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


def household_lists_sql(placeholder: str = "?") -> str:
    """SELECT of every live family-visible list of one TYPE, with its live-item count. ``placeholder`` is the driver's
    parameter marker for the type (``?`` for the compat layer, ``$1`` for raw asyncpg). The ONE query every resolver uses."""
    return (
        "SELECT l.id, l.name, l.created_at, "
        "(SELECT COUNT(*) FROM list_items i WHERE i.list_id = l.id AND i.deleted = 0) AS n "
        f"FROM lists l WHERE l.list_type = {placeholder} AND l.visibility = 'family' AND l.deleted = 0 "
        "ORDER BY l.created_at ASC, l.id ASC"
    )


def pick_household_list(rows, name: str = "") -> Optional[dict]:
    """THE household-list rule, shared by list_service, skybridge and the voice executor: among the family-visible lists of a
    TYPE the canonical one is the fullest, then the one whose name matches ``name`` (a tie-break ONLY), then the oldest -
    never the most recently touched, which would send items to an empty duplicate. Resolving by name first is how a "Shopping"
    add missed an existing family "Groceries" list and created a twin. Pure; ``rows`` are mappings with id/name/created_at/n."""
    wanted = " ".join(str(name or "").lower().split())
    cands = [dict(r) for r in rows]
    if not cands:
        return None
    best = min(range(len(cands)), key=lambda i: (
        -int(cands[i].get("n") or 0),
        0 if wanted and " ".join(str(cands[i].get("name") or "").lower().split()) == wanted else 1,
        str(cands[i].get("created_at") or ""),
        i,  # a full tie keeps the caller's order
    ))
    return cands[best]


async def find_household_list_id(db, *, list_type: str, name: str) -> Optional[str]:
    """The ONE family-visible list of ``list_type`` (see :func:`pick_household_list`). Personal lists are never candidates,
    whoever owns them."""
    cursor = await db.execute(household_lists_sql("?"), (list_type,))
    picked = pick_household_list(await cursor.fetchall(), name)
    return picked["id"] if picked else None


async def find_list_id(db, *, user_id: str, list_type: str, name: str) -> Optional[str]:
    """Resolve the list an add lands on: the household (family-visible) list of the TYPE first, whatever it is called; only when
    there is none, the user's own list of that exact name. A guest identity owns nothing: it resolves to the household list only.
    """
    household = await find_household_list_id(db, list_type=list_type, name=name)
    if household is not None or panel_principal.is_guest_id(user_id):
        return household
    cursor = await db.execute(
        "SELECT id FROM lists WHERE list_type=? AND name=? AND deleted=0 AND user_id=? LIMIT 1",
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

    Returns ``{"list_id", "item_id", "created_list", "deduped"}`` (plus ``"already": True`` and the stored ``"text"`` when the item
    was already open on the list - nothing is written then); on a dedup
    hit ``item_id`` is the existing duplicate row's id and nothing is written.
    """
    text = normalize_item_text(text) or text
    guest = panel_principal.is_guest_id(user_id)
    list_id = await find_list_id(db, user_id=user_id, list_type=list_type, name=list_name)
    if list_id is None and guest and list_type not in GUEST_CREATABLE_LIST_TYPES:
        # A guest adds to a household list that exists; it never conjures a new list of another type.
        raise HouseholdListUnavailable(list_type)
    if list_id is not None:
        if dedup_window_s > 0:
            dup_id = await _find_recent_duplicate(
                db, list_id=list_id, text=text, window_s=dedup_window_s
            )
            if dup_id is not None:
                # the item IS on the list (a replayed add), so a reply that says so is true
                write_receipts.record(user_id, "list", dup_id, "add_item_to_list:dedup")
                return {"list_id": list_id, "item_id": dup_id, "created_list": False, "deduped": True}
        # The item is already OPEN on the list (case-, plural- and punctuation-insensitive): no duplicate row; the caller says so.
        cursor = await db.execute(
            "SELECT id, text FROM list_items WHERE list_id=? AND deleted=0 AND (completed IS NULL OR completed=0)", (list_id,))
        have = find_matching_item(await cursor.fetchall(), text)
        if have is not None:
            write_receipts.record(user_id, "list", have["id"], "add_item_to_list:already")
            return {"list_id": list_id, "item_id": have["id"], "created_list": False, "deduped": True, "already": True,
                    "text": normalize_item_text(have["text"])}
        # Existing list: a single INSERT is already atomic.
        item = await create_item_record(
            db, list_id=list_id, text=text, quantity=quantity, category=category
        )
        write_receipts.record(user_id, "list", item["id"], "add_item_to_list")
        return {"list_id": list_id, "item_id": item["id"], "created_list": False, "deduped": False}

    async def _write_new_list_and_item() -> dict:
        new_list = await create_list_record(
            db,
            # A guest has no users row to own a list; the household does. A guest-created list is always family-visible.
            user_id=panel_principal.HOUSEHOLD_OWNER_ID if guest else user_id,
            name=list_name,
            list_type=list_type,
            visibility="family" if guest else new_list_visibility,
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


async def add_items_to_list(db, *, text: str, **kwargs) -> dict:
    """:func:`add_item_to_list` for a spoken add that may name several items: each item is added unless it is already open on
    the list. Returns the LAST outcome's ``list_id`` plus ``added`` / ``already`` (item texts), ``item_ids`` and ``outcomes``."""
    added, already, ids, outcomes = [], [], [], []
    # Only a SHOPPING list is a list of separate things: "call mum and dad" on a to-do list is one task.
    parts = (split_items(text) if kwargs.get("list_type") == "shopping" else [normalize_item_text(text)]) or [text]
    for part in parts:
        out = await add_item_to_list(db, text=part, **kwargs)
        outcomes.append(out)
        ids.append(out["item_id"])
        (already if out.get("already") else added).append(out.get("text") or normalize_item_text(part))
    last = outcomes[-1]
    return {**last, "item_id": ids[0] if ids else last["item_id"], "item_ids": ids, "added": added, "already": already,
            "outcomes": outcomes}
