"""The user-model CARD: the payload of the always-present user-model block.

It is a compact, structured card of CURRENT facts, one short line per category, built
deterministically (no LLM) from the user's approved memory rows. It replaced the weekly
narrative portrait as that payload, because the twin A/B (docs/knowledge/user-model-ab.md)
measured the portrait as inert: it abstracted away the specifics a reply needs ("a
vegetarian lifestyle"), and a week-old portrait re-asserted a superseded fact.

* Built by :func:`rebuild_user_model_card` in the nightly dreaming pass, by every
  portrait synthesis (``portrait_refresh`` intent, ``POST /api/portrait/…/regenerate``),
  and once lazily when a served user has no card yet. It is stored in ``user_model_cards``
  (alembic 0034) with a content-hash ``version``, so the served block stays byte-identical
  between builds.
* Served by :func:`load_card_block`, which re-checks that every source row is still
  ``approved``. A fact that is superseded, archived or forgotten during the day drops
  out on the next sidecar fetch. The card is never served stale, and a card with no
  live fact is "".
* Every rule is in the tables below. They are pure and unit-tested in
  ``tests/test_user_model_card.py``.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CARD_MAX_CHARS = 1400      # ≈ 350 tokens (the research budget, §4 item 1)
ITEM_MAX_CHARS = 90
STYLE_MAX_CHARS = 200
SCAN_LIMIT = 5000          # list_by_status reads every row then slices; this is a guard
# Time-bound lines (Current) drop a fact older than the store's own recency half-life
# (memory_service._metadata_read HALF_LIFE_DAYS = 70). A relative date in it ("next
# Friday") has stopped meaning anything by then; recall_memory still has the row.
CURRENT_MAX_AGE_DAYS = 70

# Rows that describe the person. Everything else is left out: notes, journal entries,
# insights, open loops, the profile-analysis JSON, failures and capabilities.
ALLOWED_TYPES = frozenset({"fact", "profile", "preference", "habit", "event",
                           "relationship", "health", "pet", "person"})
EXCLUDED_SOURCES = ("synthesis", "profile-analysis", "note_", "journal_")
PER_SOURCE_CAP = {"music_digest": 1}  # nightly music taste rows would flood "Enjoys"
PEOPLE_TYPES = frozenset({"relationship", "pet", "person"})
# memory_supersede's tombstone type/tag. Not in ALLOWED_TYPES; the tag check also
# covers a tombstone whose type a later edit changed.
STATE_CHANGE = "state_change"

_ROLES = (r"wife|husband|partner|fianc[eé]e?|girlfriend|boyfriend|sons?|daughters?|kids?|"
          r"child(?:ren)?|brothers?|sisters?|siblings?|mum|mom|mother|dad|father|parents?|"
          r"grand\w+|aunt|uncle|cousin|niece|nephew|(?:best )?friends?|boss|colleagues?|"
          r"flatmate|roommate|neighbou?r|dogs?|cats?|pets?|pupp(?:y|ies)|kittens?|greyhound|"
          r"horse|birds?|rabbits?|parrot")
# "User's (old) greyhound …" / "User has a dog named …": the fact is about someone else.
_PEOPLE_SUBJECT = re.compile(
    rf"^(?:the\s+)?user(?:'s\s+(?:\w+\s+)?|\s+has\s+(?:an?|one|two|three|\d+)\s+(?:\w+\s+)?)(?:{_ROLES})\b",
    re.I)


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.I)


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    pattern: re.Pattern[str] | None
    types: frozenset[str]            # memory_type fallback when no pattern matched
    max_items: int
    max_age_days: int | None = None  # None = stable identity, no age limit
    dated: bool = False              # append "(noted 29 Sep)"


# MATCH order: the first category whose pattern matches wins, then the type fallback.
# People go first (a sister's diet is not the user's diet), About before Current (a
# birthday is not a plan), and Current before Work ("working on …" is a project).
CATEGORIES: tuple[Category, ...] = (
    Category("people", "People & pets", _PEOPLE_SUBJECT, PEOPLE_TYPES, 5),
    Category("about", "About", _rx(r"birthday", r"born", r"years old", r"goes by", r"nickname",
                                   r"pronouns"), frozenset({"profile"}), 2),
    Category("prefers", "Prefers", _rx(r"answers?", r"repl(?:y|ies)", r"responses?",
                                       r"explanations?", r"bullet points?", r"small talk",
                                       r"tone", r"jargon", r"emojis?"), frozenset(), 2),
    Category("diet", "Diet", _rx(
        r"vegetarian", r"vegan", r"pescatarian", r"meat", r"fish", r"seafood", r"shellfish",
        r"dairy", r"lactose", r"gluten", r"coeliac", r"celiac", r"peanuts?", r"nuts?",
        r"alcohol", r"drinks?", r"drinking", r"sober", r"teetotal", r"coffee", r"tea",
        r"caffeine", r"coriander", r"cilantro", r"spicy", r"diet", r"eats?", r"eating",
        r"foods?", r"cook(?:s|ing)?", r"recipes?", r"halal", r"kosher", r"keto",
        r"breakfast", r"lunch", r"dinner", r"snacks?"), frozenset(), 4),
    Category("health", "Health", _rx(
        r"allerg\w*", r"asthma", r"diabet\w*", r"epilep\w*", r"migraines?", r"injur\w*",
        r"surgery", r"medication", r"meds", r"pregnan\w*", r"chronic", r"arthritis", r"adhd",
        r"blood pressure", r"knee", r"back pain", r"insomnia", r"deaf", r"hearing",
        r"wheelchair", r"disab\w*", r"epipen"), frozenset({"health"}), 3),
    Category("current", "Current", _rx(
        r"training", r"learning", r"studying", r"preparing", r"planning", r"plans?",
        r"working on", r"project", r"goals?", r"trying to", r"saving (?:up )?for", r"trip",
        r"holiday", r"vacation", r"audition", r"exam", r"interview", r"moving", r"wedding",
        r"race", r"(?:half[- ])?marathon", r"10 ?k", r"5 ?k", r"triathlon", r"ironman",
        r"fun run", r"upcoming",
        r"next (?:week|month|year|weekend|spring|summer|autumn|fall|winter|\w+day)",
        r"this (?:week|month|year|weekend)",
        r"(?:in|on|by) (?:january|february|march|april|may|june|july|august|september|"
        r"october|november|december)"), frozenset({"event"}), 3, CURRENT_MAX_AGE_DAYS, True),
    Category("work", "Work & schedule", _rx(
        r"works?", r"job", r"shifts?", r"nurse", r"doctor", r"teacher", r"engineer",
        r"developer", r"office", r"boss", r"colleagues?", r"career", r"employ\w*", r"retired",
        r"student", r"university", r"college", r"freelanc\w*", r"business", r"company",
        r"commute", r"role", r"sleeps? during the day"), frozenset(), 3),
    Category("places", "Places", _rx(
        r"lives? in", r"lived in", r"living in", r"is from", r"comes from", r"grew up",
        r"moved (?:to|from)", r"home ?town", r"visited", r"travel(?:s|led|ling)?"),
        frozenset(), 2),
    Category("enjoys", "Enjoys", _rx(
        r"plays?", r"playing", r"hobb(?:y|ies)", r"loves", r"enjoys", r"likes", r"fan of",
        r"reads", r"reading", r"garden\w*", r"music", r"guitar", r"piano", r"cello",
        r"violin", r"drums", r"sings?", r"choir", r"orchestra", r"band", r"paint\w*",
        r"hik\w*", r"running", r"runs", r"cycl\w*", r"swim\w*", r"yoga", r"gym", r"football",
        r"soccer", r"cricket", r"tennis", r"golf", r"chess", r"gaming", r"knit\w*",
        r"photograph\w*", r"podcasts?", r"films?", r"movies", r"books?", r"favou?rite"),
        frozenset({"preference", "habit"}), 3),
)
# RENDER order (and the budget cut drops from the END of this order first).
RENDER_ORDER = ("prefers", "diet", "health", "work", "people", "current", "enjoys",
                "places", "about")

# Writer boilerplate to strip (memory_extractor._TEMPLATE_PATTERNS, turn digest's
# third-person "User …" facts), leaving the fact itself.
_LEADS = (
    (re.compile(r"^(?:user asked me to remember|important note|preference|favou?rite)\s*:\s*",
                re.I), ""),
    (re.compile(r"^user's job/role:\s*", re.I), "role: "),
    (re.compile(r"^(?:the\s+)?user(?:'s)?\s+(?:is\s+)?", re.I), ""),
)
_NAME_FACT = re.compile(r"^(?:the\s+)?user's\s+(?:full\s+)?name\s+is\b", re.I)
_CONTACT = re.compile(r"\S+@\S+\.\w+|(?:\+?\d[\s().-]?){7,}")  # e-mail / phone number
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_STYLE = _rx(r"communicat\w*", r"prefers?", r"direct", r"brief", r"concise", r"gentle",
             r"to the point", r"straightforward", r"listen\w*", r"speaks?", r"talks?", r"tone")


def _parse_ts(value: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _cap(text: str, limit: int) -> str:
    """≤ ``limit`` chars, cut at a word when that keeps at least half."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space >= limit // 2 else cut).rstrip(",;: ") + "…"


def compact_fact(text: str) -> str:
    """'User is allergic to peanuts.' → 'allergic to peanuts'; '' when unusable."""
    from memory_service import scrub_pii  # type: ignore[import]

    t = re.sub(r"\s+", " ", text or "").strip().rstrip(".!")
    if not t or t[0] in "{[" or _NAME_FACT.match(t):
        return ""
    for rx, repl in _LEADS:
        t = rx.sub(repl, t, count=1)
    t, reject = scrub_pii(t)
    if reject or "[REDACTED]" in t or _CONTACT.search(_ISO_DATE.sub("", t)) or len(t) < 3:
        return ""
    return _cap(t, ITEM_MAX_CHARS)


def categorize(text: str, meta: dict[str, Any]) -> Category | None:
    mtype = str(meta.get("memory_type") or "fact")
    if mtype in PEOPLE_TYPES or str(meta.get("entity_type") or "").startswith("person"):
        return CATEGORIES[0]
    for cat in CATEGORIES:
        if cat.pattern is not None and cat.pattern.search(text):
            return cat
    return next((c for c in CATEGORIES if mtype in c.types), None)


def _eligible(meta: dict[str, Any]) -> bool:
    source = str(meta.get("source") or "")
    return (str(meta.get("status") or "approved") == "approved"
            # a recorded change ("dropped the half-marathon") is never a current fact
            and STATE_CHANGE not in str(meta.get("tags") or "").split(",")
            and str(meta.get("memory_type") or "fact") in ALLOWED_TYPES
            and not source.startswith(EXCLUDED_SOURCES)
            and not meta.get("expires_at")          # time-bound rows: recall/continuity own them
            and not meta.get("candidate_affect"))   # feelings age by the hour: continuity owns them


def style_line(portrait: str | None) -> str:
    """The narrative portrait's first 'how they communicate' sentence, or ''."""
    from memory_service import scrub_pii  # type: ignore[import]

    for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", portrait or "").strip()):
        if _STYLE.search(sentence):
            s, reject = scrub_pii(sentence)
            return "" if reject or "[REDACTED]" in s else _cap(s, STYLE_MAX_CHARS)
    return ""


def build_card(name: str, rows: Iterable[Any], portrait: str | None = None,
               *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Pure: ``{"name", "style", "items": [[category, text, mem_id], …]}``.

    Newest fact wins: rows are taken newest first (id breaks ties, so the same rows always
    give the same card). An item whose words are all contained in an item already taken
    is a duplicate and is skipped.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    ordered = sorted(rows, key=lambda r: (str((r.metadata or {}).get("added_at") or ""), r.id),
                     reverse=True)
    by_cat: dict[str, list[list[str]]] = {c.key: [] for c in CATEGORIES}
    seen: list[set[str]] = []
    per_source: dict[str, int] = {}
    for ref in ordered:
        meta = dict(ref.metadata or {})
        if not _eligible(meta):
            continue
        cat = categorize(ref.text or "", meta)
        if cat is None or len(by_cat[cat.key]) >= cat.max_items:
            continue
        added = _parse_ts(meta.get("added_at"))
        if cat.max_age_days is not None and (added is None
                                             or (now - added).days > cat.max_age_days):
            continue
        source = str(meta.get("source") or "")
        if per_source.get(source, 0) >= PER_SOURCE_CAP.get(source, 1 << 30):
            continue
        text = compact_fact(ref.text or "")
        words = set(re.findall(r"\w+", text.lower()))
        if not text or any(words <= s for s in seen):
            continue
        if cat.key == "prefers":
            text = re.sub(r"^prefers\s+", "", text)  # "Prefers: prefers short answers"
        if cat.dated and added is not None:
            text = f"{text} (noted {added.day} {added:%b})"
        seen.append(words)
        per_source[source] = per_source.get(source, 0) + 1
        by_cat[cat.key].append([cat.key, text, ref.id])
    items = [it for key in RENDER_ORDER for it in by_cat[key]]
    return {"name": re.sub(r"\s+", " ", name or "").strip(), "style": style_line(portrait),
            "items": items}


def render_card(card: dict[str, Any], live_ids: set[str] | None = None) -> str:
    """Pure: the card text, ≤ CARD_MAX_CHARS; '' when no (live) fact item.

    ``live_ids`` keeps only those items (None keeps all). Over budget, the last item of the
    last non-empty category in RENDER_ORDER is dropped, repeatedly.
    """
    items = [it for it in card.get("items") or [] if live_ids is None or it[2] in live_ids]
    labels = {c.key: c.label for c in CATEGORIES}
    head = [f"Name: {card['name']}"] if card.get("name") else []
    tail = [f"How they talk (weekly portrait): {card['style']}"] if card.get("style") else []
    while items:
        lines = [f"{labels[key]}: " + "; ".join(t for k, t, _ in items if k == key)
                 for key in RENDER_ORDER if any(k == key for k, _, _ in items)]
        text = "\n".join(head + lines + tail)
        if len(text) <= CARD_MAX_CHARS:
            return text
        if tail:
            tail = []
            continue
        last = max(range(len(items)), key=lambda i: (RENDER_ORDER.index(items[i][0]), i))
        items.pop(last)
    return ""


def card_version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16] if text else ""


# ── Storage (user_model_cards, alembic 0034) ─────────────────────────────────────────

_UPSERT = """INSERT INTO user_model_cards (user_id, card_json, card_text, version, built_at,
                                           source_count)
             VALUES (?, ?, ?, ?, ?, ?)
             ON CONFLICT(user_id) DO UPDATE SET card_json = excluded.card_json,
                 card_text = excluded.card_text, version = excluded.version,
                 built_at = excluded.built_at, source_count = excluded.source_count"""


async def _display_name(db, user_id: str) -> str:
    row = await (await db.execute("SELECT name FROM users WHERE id = ?", (user_id,))).fetchone()
    name = ((row[0] if row else "") or "").strip()
    return name.title() if name.islower() else name


async def rebuild_user_model_card(user_id: str, db=None) -> dict[str, Any]:
    """Build and store one user's card. It is a no-op unless the block would be served
    for this id (``user_portrait.user_model_enabled``). Never raises."""
    from user_portrait import load_portrait, user_model_enabled  # type: ignore[import]

    uid = (user_id or "").strip()
    if not user_model_enabled(uid):
        return {"status": "disabled"}
    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        from memory_service import get_memory_service  # type: ignore[import]

        rows = await get_memory_service().list_by_status(user_id=uid, status="approved",
                                                         limit=SCAN_LIMIT)
        portrait = await load_portrait(uid, db=db, max_chars=0)

        async def _write(conn) -> dict[str, Any]:
            card = build_card(await _display_name(conn, uid), rows, portrait)
            text = render_card(card)
            version = card_version(text)
            built = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            await conn.execute(_UPSERT, (uid, json.dumps(card, ensure_ascii=False), text,
                                         version, built, len(rows)))
            await conn.commit()
            return {"status": "ok", "items": len(card["items"]), "chars": len(text),
                    "version": version}
        if db is not None:
            out = await _write(db)
        else:
            async with get_db_ctx() as conn:
                out = await _write(conn)
        logger.info("user-model card: built user=%s items=%d chars=%d version=%s", uid,
                    out["items"], out["chars"], out["version"] or "-")
        return out
    except Exception as exc:
        logger.warning("user-model card: build failed user=%s: %s", uid, exc)
        return {"status": "error", "error": str(exc)}


async def _live_ids(user_id: str, ids: list[str]) -> set[str] | None:
    """The ids whose memory row is still approved; None when the store cannot say."""
    from memory_service import get_memory_service  # type: ignore[import]

    svc, live = get_memory_service(), set()
    try:
        for mem_id in ids:
            ref = await svc.get(mem_id)
            if ref is not None and str((ref.metadata or {}).get("status")) == "approved":
                live.add(mem_id)
    except Exception as exc:
        logger.warning("user-model card: liveness check failed user=%s: %s", user_id, exc)
        return None
    return live


async def load_card_block(user_id: str, *, build_missing: bool = True) -> dict[str, str]:
    """``{"version", "text"}`` for an id already cleared by ``user_model_enabled``.

    It serves the stored card when every source row is still approved, which is the
    common case and byte-identical to the build. Otherwise it re-renders the live subset.
    A missing card is built once. If the liveness check fails, the stored card is served
    (fail open: an outage must not bust the prompt cache).
    """
    from db_pool import get_db_ctx  # type: ignore[import]

    empty = {"version": "", "text": ""}
    try:
        async with get_db_ctx() as db:
            row = await (await db.execute(
                "SELECT card_json, card_text, version FROM user_model_cards WHERE user_id = ?",
                (user_id,))).fetchone()
    except Exception as exc:
        logger.debug("user-model card: load failed (non-fatal) user=%s: %s", user_id, exc)
        return empty
    if row is None:
        if build_missing and (await rebuild_user_model_card(user_id)).get("status") == "ok":
            return await load_card_block(user_id, build_missing=False)
        return empty
    card, text, version = json.loads(row[0] or "{}"), row[1] or "", row[2] or ""
    ids = [it[2] for it in card.get("items") or []]
    live = await _live_ids(user_id, ids)
    if live is None or len(live) == len(set(ids)):
        return {"version": version, "text": text}
    text = render_card(card, live)
    return {"version": card_version(text), "text": text}
