"""
LLM-driven nightly memory digest.

Reads today's chat messages for a user, prompts Gemma to extract personal
facts as structured JSON, deduplicates against existing MemPalace records,
and writes new facts through MemoryService (the sole memory writer).

Usage:
    result = await run_memory_digest(user_id="jason", db=db_session)
    # {"user_id": "jason", "extracted": 5, "new": 3, "skipped_duplicates": 2}

Scheduled by routers/system.py at 3am daily.
Manual trigger: POST /api/memories/digest?user_id=jason
"""
import asyncio
import json
import logging
import os
import re
import uuid

import httpx
import memory_authority
import own_words
from memory_overlap import dedup_verdict, richness
from routers.journal import CREATED_AT_VALID_TIMESTAMP_SQL
from user_filters import GUEST_USERS, drop_synthetic_users, message_owner_expr

logger = logging.getLogger(__name__)


def _normalize_gemma_base(raw: str) -> str:
    """Base URL for the local llama-server, WITHOUT a trailing /v1.

    Call sites here append `/v1/chat/completions`. But `GEMMA_SERVER_URL` is shared
    with other modules (e.g. zoe_agent) whose convention INCLUDES `/v1` — and the
    live systemd unit sets it that way. Without this strip, this module produces
    `/v1/v1/chat/completions` → 404 and silently breaks extraction/consolidation.
    Normalize so the appends below are correct regardless of whether the env value
    ends in /v1.
    """
    base = (raw or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base or "http://127.0.0.1:11434"


_GEMMA_URL = _normalize_gemma_base(os.environ.get("GEMMA_SERVER_URL", "http://127.0.0.1:11434"))
_ZOE_TIMEZONE = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")

# Rolling lookback for the nightly digest, in hours. 30h (not 24h) so a 03:00
# run covers the whole previous calendar day plus the 3h offset, with slack for
# a late or retried run. Overlap between nights is harmless — the extractor
# dedupes (skipped_duplicates in the effects counters).
_DIGEST_LOOKBACK_DEFAULT = 30
#: A 03:00 run needs more than 27h to reach the whole previous calendar day.
_DIGEST_LOOKBACK_MIN = 27


def _digest_lookback_hours() -> int:
    """Parse the lookback, refusing values that would silently break the digest.

    A bare int() has two quiet failure modes, both of which are exactly the
    class of bug this constant was introduced to fix: a typo ("30h") raises at
    IMPORT time and takes the module down when the scheduled loop reaches it,
    and 0 (or anything under the floor) silently recreates the empty window that
    processed nobody for ten consecutive nights.
    """
    raw = (os.environ.get("ZOE_MEMORY_DIGEST_LOOKBACK_HOURS") or "").strip()
    if not raw:
        return _DIGEST_LOOKBACK_DEFAULT
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "memory_digest: ZOE_MEMORY_DIGEST_LOOKBACK_HOURS=%r is not an integer; "
            "using %dh", raw, _DIGEST_LOOKBACK_DEFAULT)
        return _DIGEST_LOOKBACK_DEFAULT
    if hours < _DIGEST_LOOKBACK_MIN:
        logger.warning(
            "memory_digest: ZOE_MEMORY_DIGEST_LOOKBACK_HOURS=%d is below the %dh floor "
            "(a 03:00 run needs the whole previous day); using %dh",
            hours, _DIGEST_LOOKBACK_MIN, _DIGEST_LOOKBACK_DEFAULT)
        return _DIGEST_LOOKBACK_DEFAULT
    return hours


_DIGEST_LOOKBACK_HOURS = _digest_lookback_hours()
_GUEST_USERS = GUEST_USERS  # single source: user_filters.GUEST_USERS

_LINK_RESOLVER_TRUTHY = frozenset({"1", "true", "yes", "on"})


def memory_link_resolver_enabled() -> bool:
    """Cheap per-call read of the idle person-link resolver flag (default OFF).

    When ON, the dream cycle re-links ``person_pending`` facts to a real
    ``people.id`` once the contact exists (see ``_resolve_pending_person_links``).
    A true no-op while OFF — the resolver returns before any store/DB access.
    """
    return (
        os.environ.get("ZOE_MEMORY_LINK_RESOLVER_ENABLED", "").strip().lower()
        in _LINK_RESOLVER_TRUTHY
    )


def _name_from_pending_slug(entity_id: str) -> str:
    """Human name from a ``slug:<body>`` entity_id (person_extractor convention).

    ``"slug:mary_jane"`` → ``"mary jane"``. A bare value (no ``slug:`` prefix) is
    de-underscored as-is so a legacy pending id still resolves.
    """
    s = (entity_id or "").strip()
    if s.startswith("slug:"):
        s = s[len("slug:"):]
    return s.replace("_", " ").strip()


async def _resolve_pending_person_links(user_id: str, db=None) -> dict:
    """Idle pass: re-link ``person_pending`` facts to a real ``people.id``.

    Scans this user's ``person_pending`` memories, resolves the slug'd name via
    ``person_extractor._resolve_person_uuid``, and — when a contact now exists —
    rewrites ``entity_id`` → the ``people.id`` and flips ``entity_type``
    ``person_pending`` → ``person``. The rewrite is a **metadata-only**
    ``col.update`` (no document → Chroma does NOT re-embed), the same path
    ``tick_access`` uses.

    Default-OFF: a true no-op (no DB open, no store scan) unless
    ``ZOE_MEMORY_LINK_RESOLVER_ENABLED`` is set. Runs only in the idle dream
    cycle, so it never touches the turn path.
    """
    result = {"user_id": user_id, "scanned": 0, "relinked": 0}
    if not memory_link_resolver_enabled():
        return result

    from person_extractor import _ensure_db
    from memory_extractor import _resolve_unique_person_uuid
    from memory_service import get_memory_service, is_guest_memory_user, leased_drawers

    if not user_id or is_guest_memory_user(user_id):
        return result

    _db, opened = await _ensure_db(db)
    if _db is None:
        return result
    try:
        svc = get_memory_service()
        col = leased_drawers(svc)   # per-call lease: never a handle across an await
        # Owner + status scoped scan: only THIS user's still-pending person facts.
        results = col.get(
            where={"$and": [
                {"user_id": {"$eq": user_id}},
                {"entity_type": {"$eq": "person_pending"}},
            ]},
            include=["metadatas"],
        )
        ids = results.get("ids") or []
        metas = results.get("metadatas") or []
        for mem_id, meta in zip(ids, metas):
            meta = dict(meta) if meta else {}
            result["scanned"] += 1
            name = _name_from_pending_slug(str(meta.get("entity_id") or ""))
            if not name:
                continue
            # Unambiguous match only — never guess "Sam" onto "Samantha" and
            # permanently rewrite the fact to the wrong person.
            person_uuid = await _resolve_unique_person_uuid(name, user_id, _db)
            if not person_uuid:
                continue
            # Relink through the memory service so the metadata-only rewrite runs
            # under the SAME per-user lock as tick_access (no lost-update race).
            if await svc.relink_entity(user_id, mem_id, "person", str(person_uuid)):
                result["relinked"] += 1
                logger.info(
                    "link_resolver: relinked %s -> person %s user=%s",
                    mem_id, person_uuid, user_id,
                )
    except Exception as exc:
        logger.warning("link_resolver: scan failed user=%s: %s", user_id, exc)
    finally:
        if opened and _db is not None:
            try:
                await _db.close()
            except Exception:
                pass
    return result


def _count_drop(source: str, guard: str, *, gate: bool = True) -> None:
    """Put one guard / dedup drop in the reject ledger (reason ``guard_<guard>``). Never raises."""
    try:
        from memory_reject_ledger import record_guard_drop
        record_guard_drop(source, guard, gate=gate)
    except Exception:  # noqa: BLE001 - bookkeeping must never block a write path
        pass


def _passes_quality_gate(text: str) -> bool:
    """Quality gate for the digest/synthesis LLM passes, which occasionally emit
    non-facts ("The provided facts illustrate…", transcript echoes) that then
    pollute recall. Mirrors the gate the conversational writers already apply.
    Degrades to accept if the gate is unavailable so we never silently drop a
    real fact."""
    try:
        from memory_quality import is_storable_fact
        ok, reason = is_storable_fact(text)
        if not ok:
            # This gate used to drop silently (reason discarded, no log, no counter).
            from memory_reject_ledger import record_reject
            record_reject("digest", reason)
        return ok
    except Exception:
        return True


# Moved to user_filters so proactive/ can share it without importing this module.
_message_owner_expr = message_owner_expr


def _message_owner_users_sql(*, today_only: bool, lookback_hours: int | None = None,
                             cutoff: bool = False) -> str:
    """Users with chat activity, optionally windowed.

    ``lookback_hours`` selects a ROLLING window ending now and takes precedence
    over ``today_only``. With ``cutoff=True`` the window instead ends at a
    caller-supplied ``?::timestamptz`` (bound as ``(cutoff, hours, cutoff)``),
    so the nightly pass can hand the SAME instant to selection and to the
    input probe — otherwise a turn landing between the two calls reads as
    "turns exist but nobody was selected" (Greptile P2 on #1682).

    It exists because ``today_only`` is calendar-day based and the nightly
    digest fires at 03:00: "today" was therefore a three-hour-old window
    (00:00-03:00), while the conversations the job exists to digest happened the
    previous day and fell on the far side of midnight. Measured effect: 10
    consecutive nightly runs (2026-07-11..07-20) each completed cleanly and
    reported 0 users processed with all-zero effects.
    """
    owner_expr = _message_owner_expr()
    date_clause = ""
    if lookback_hours is not None and cutoff:
        date_clause = """
          AND cm.created_at::timestamptz >=
              (?::timestamptz - make_interval(hours => ?::int))
          AND cm.created_at::timestamptz < ?::timestamptz
        """
    elif lookback_hours is not None:
        # Same cast discipline as the calendar clause below: through the
        # positional-compat layer an uncast placeholder binds as "unknown" and
        # overload resolution fails, silently zeroing the result rather than
        # erroring. (Keep this SQL free of literal question marks, including in
        # comments — the compat layer counts them as placeholders.)
        date_clause = """
          AND cm.created_at::timestamptz >=
              (now()::timestamptz - make_interval(hours => ?::int))
        """
    elif today_only:
        # Casts are load-bearing. Through the asyncpg positional-compat layer, an
        # uncast timezone placeholder binds as "unknown" and the timestamp operand
        # collapses to text, so overload resolution fails ("function
        # pg_catalog.timezone(unknown, text) does not exist") and the whole
        # discovery query errors — silently zeroing out active-user detection.
        # The ::text zone cast + now()::timestamptz pin the timezone(text,
        # timestamptz) overload. (Keep this SQL free of literal question marks,
        # including in comments — the compat layer counts them as placeholders.)
        date_clause = """
          AND (cm.created_at::timestamptz AT TIME ZONE ?::text)::date =
              (now()::timestamptz AT TIME ZONE ?::text)::date
        """
    return f"""
        SELECT DISTINCT owner.user_id
        FROM (
            SELECT {owner_expr} AS user_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'user'
            {date_clause}
        ) owner
        WHERE owner.user_id IS NOT NULL
        """

def _shared_rules() -> str:
    """The date-order + no-guessed-roles lines every fact-extraction prompt carries
    (date_locale.PROMPT_RULE, people_roles.PROMPT_RULES) — one source, so the turn digest,
    the nightly digest and the idle consolidation cannot drift apart."""
    from date_locale import PROMPT_RULE
    from people_roles import PROMPT_RULES

    return f"{PROMPT_RULE}\n{PROMPT_RULES}\n"


_SHARED_RULES = _shared_rules()

_EXTRACTION_PROMPT = """\
You are extracting personal facts from a chat transcript. Only extract facts the user explicitly stated about themselves, their family, preferences, or life. Do NOT infer, assume, or add anything not stated directly.

Return ONLY a JSON array (no preamble, no explanation). Each item has:
  "type": one of "profile" | "preference" | "habit" | "event" | "relationship" | "health"
  "fact": a single concise sentence (max 150 chars) in third-person (e.g. "User is 44 years old")
  "quote": the user's OWN words that state it, copied EXACTLY from ONE chat message below (a fact about the user needs words where the user speaks about themself: "I", "my", "me"). A name that merely appears in a message is not a fact about the user.

If nothing personal was stated, return: []

""" + _SHARED_RULES + """
Chat messages (user turns only):
{chat_text}
"""


# For contradiction checks we want a single-token yes/no so the decision is
# cheap and unambiguous. The schema lets us also capture *which* existing fact
# is contradicted when multiple candidates are evaluated at once.
# Temperature=0 removes jitter.
_CONTRADICTION_PROMPT = """\
You are judging whether a NEW fact contradicts an EXISTING fact about the same person.

Two facts CONTRADICT only if they cannot both be true at the same time about the same subject
(e.g. "User lives in Sydney" vs "User lives in Melbourne" — contradiction;
 "User likes coffee" vs "User likes tea" — NOT a contradiction, both can be true).

NEW fact:
{new_fact}

EXISTING fact:
{existing_fact}

Return ONLY one JSON object, nothing else:
  {{"contradicts": true|false, "reason": "<=15 words"}}
"""


# ── Emotional memory extraction ───────────────────────────────────────────────
# Identifies emotionally significant moments from conversations. Stored with
# memory_type="emotional_moment" so the agent can surface them as relationship
# context — the emotional arc of the user's life, not just facts about it.
_EMOTIONAL_EXTRACTION_PROMPT = """\
You are identifying emotionally significant moments from a conversation.

Look for: strong emotions the person expressed, important personal news they shared, \
moments where the interaction carried real emotional weight.

Return ONLY a JSON array (or [] if nothing qualifies):
  "moment": what happened, written in third-person, max 150 chars
  "emotion": one of joy | excitement | anxiety | sadness | frustration | pride | relief | love | grief | other
  "significance": integer 1-3  (1=minor, 2=notable, 3=major life moment)

Only include moments with significance >= 2. If nothing qualifies, return [].

Conversation (user turns only):
{chat_text}
"""


_TURN_EXTRACTION_PROMPT = """\
You are extracting personal facts from a single chat exchange. Only extract facts the user explicitly stated about themselves, their family, pets, preferences, or life. Do NOT infer or assume anything not stated directly.

Return ONLY a JSON array (no preamble). Each item:
  "type": one of "profile" | "preference" | "habit" | "event" | "relationship" | "health" | "pet"
  "fact": a single concise sentence in third-person (max 120 chars, e.g. "User's dog is named Teddy")

If the user said how they FEEL about it (anxious, worried, excited, sad, stressed, proud…), keep that feeling in the fact — e.g. "User is anxious about their job interview on Friday", not just "User has a job interview on Friday". Never add a feeling the user did not state.

If the user names a person AND says something is happening with them (a trip, visit, arrival, move, plan), return BOTH: one fact for who the person is, and one fact for the event that keeps who, what, where and when — e.g. for "my brother Tomás is driving down from Porto on Saturday": "User's brother is named Tomás" AND "User's brother Tomás is driving down from Porto on Saturday". Never drop the place or the day.

If nothing personal was stated, return: []

""" + _SHARED_RULES + """
User said: {user_message}
"""


_AFFECT_STOPWORDS = frozenset({
    "user", "users", "user's", "their", "they", "about", "with", "that", "this",
    "have", "has", "will", "from", "into", "when", "what", "been", "being",
    "honestly", "pretty", "really", "feel", "feels", "feeling", "keep",
    # time words: two facts that merely share a day are not the same topic
    # ("…interview on Friday. My sister arrives Friday" — Greptile #1762)
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "today", "tonight", "tomorrow", "yesterday", "morning", "afternoon", "evening",
    "week", "weekend", "month", "year", "next", "last", "later", "soon",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
})
_FACT_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# The feeling words themselves say nothing about WHICH fact the feeling is
# about, so they never count toward attribution.
_AFFECT_WORDS = frozenset({
    "anxious", "anxiety", "nervous", "edge", "uneasy", "worried", "worrying",
    "dreading", "stressed", "stressing", "pressure", "scared", "afraid",
    "terrified", "frightened", "overwhelmed", "swamped", "down", "miserable",
    "heartbroken", "gutted", "upset", "lonely", "frustrated", "annoyed",
    "exhausted", "drained", "burnt", "burned", "worn", "excited", "thrilled",
    "pumped", "happy", "delighted", "proud", "relieved", "wait",
})


def _content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']+", (text or "").lower())
            if len(t) > 3 and t not in _AFFECT_STOPWORDS}


# Words that describe a MOOD rather than name a thing ("a bit rough lately").
# Only used to decide whether a stored fact has a topic at all.
_MOOD_FILLER_WORDS = frozenset({
    "rough", "tough", "awful", "terrible", "stressful", "lately", "quite",
    "little", "very", "much", "kind", "sort", "generally", "overall", "mood",
    "vibe", "vibes", "day", "days", "night", "nights", "bad", "hard", "long",
    "tired", "sleepy", "grumpy", "cranky", "irritable", "moody", "restless",
    "jittery", "tense", "emotional", "great", "good", "okay", "fine", "better",
    "worse", "lonely", "blue",
    # past / perfect forms of a mood report ("User felt down today", "User has
    # been feeling on edge", "User seemed tense", "User got overwhelmed") and
    # "a rough time / moment" — Greptile #1768
    "felt", "seemed", "seems", "seem", "seeming", "getting", "gotten", "became",
    "become", "becoming", "time", "times", "moment", "moments", "somewhat",
    "rather", "wasn't", "isn't", "hasn't", "feelings",
})


def fact_has_topic(text: str) -> bool:
    """True when a stored fact names SOMETHING beyond a mood — a thing a
    check-in could ask about ("…anxious about their job interview at the
    aquarium") — and False for a bare mood report ("User has been feeling a bit
    on edge today"). Content words only: stopwords, time words, feeling words
    and mood fillers never count.

    A bare mood report is usually what the user is saying RIGHT NOW (the digest
    stores today's "I'm on edge" seconds after the turn), so it must never be
    the continuity focus: "how is feeling on edge going?" is not a check-in
    (Samantha bar S4 round 3)."""
    return bool(_content_tokens(text) - _AFFECT_WORDS - _MOOD_FILLER_WORDS)


def _affect_for_fact(fact: str, affect: str, sentence: str, message: str = "") -> str:
    """The turn's first-person feeling, if this fact came from the sentence that
    carried it — else "". Sentence-level attribution, not a shared word:

    * content words only — stopwords, time words (weekdays, months, "today"…)
      and the feeling words themselves never count, so a shared "Friday" proves
      nothing;
    * the fact must share at least one content word with the feeling sentence
      (the digest often SHORTENS: "User has a job interview on Friday" keeps only
      "interview" from "…anxious about my job interview at the aquarium…");
    * and the feeling sentence must win UNIQUELY — strictly more shared words
      than any other sentence of the message. A fact that belongs to a
      neighbouring sentence ("My sister arrives Friday") loses, and a tie is
      ambiguous and attaches nothing.
    """
    if not affect or not sentence:
        return ""
    fact_tokens = _content_tokens(fact)
    overlap = len(fact_tokens & (_content_tokens(sentence) - _AFFECT_WORDS))
    if overlap < 1:
        return ""
    feel_norm = sentence.strip()
    for other in _FACT_SENTENCE_SPLIT_RE.split(message or ""):
        other = other.strip()
        if not other or other == feel_norm or feel_norm in other:
            continue
        if len(fact_tokens & (_content_tokens(other) - _AFFECT_WORDS)) >= overlap:
            return ""
    return affect


#: A row's wording is logged whole up to this many characters (a distilled sentence is <= 150 by the extractor prompt).
_ROW_LOG_WORDING_MAX = 200


def log_row(lane: str, user_id: str, ref, outcome: str) -> None:
    """ONE INFO line per row a digest stored or parked, so a live incident can be REPLAYED instead of guessed at
    (the day-sim 6n diagnosis had no record of what the extractor wrote or how the row was classed):

        MEMORY_ROW lane=<turn_digest|digest|emotional> outcome=<stored|parked|edited|held> user=<id> id=<row id>
        class=<authority class> promoted=<yes|no> basis=<authority basis> status=<row status> type=<memory type>
        wording='<the ROW text, as stored>'

    ``promoted`` is the promotion verdict: yes = the owner's one verbatim sentence ENTAILED the fact
    (``memory_authority.VERBATIM_BASIS``), so a retraction earns the standing of the owner's own words; no = it did
    not (a candidate is parked as a dispute instead). The wording is the row's own text - the extractor's distilled
    sentence after the write boundary's scrub - never the owner's turn (no ``source_excerpt`` / anchor is read here).
    Post-turn only (the background digest); never raises; ``%r`` so a newline in a wording cannot forge a line."""
    try:
        if ref is None:
            return
        md = getattr(ref, "metadata", None) or {}
        basis = str(md.get("authority_basis") or "-")
        wording = " ".join(str(getattr(ref, "text", "") or "").split())[:_ROW_LOG_WORDING_MAX]
        logger.info(
            "MEMORY_ROW lane=%s outcome=%s user=%s id=%s class=%s promoted=%s basis=%s status=%s type=%s wording=%r",
            lane, outcome, user_id, getattr(ref, "id", "-"), md.get("authority_class") or "-",
            "yes" if basis == memory_authority.VERBATIM_BASIS else "no", basis,
            md.get("status") or "-", md.get("memory_type") or "-", wording)
    except Exception:  # noqa: BLE001 - a log line must never fail a digest
        pass


def _implicit_change_cue(user_message: str) -> str | None:
    """The utterance's change-of-state cue when ZOE_MEMORY_IMPLICIT_SUPERSEDE is on,
    else None (memory_supersede; off = the turn digest is unchanged)."""
    try:
        import memory_supersede

        return memory_supersede.utterance_cue(user_message) if memory_supersede.enabled() else None
    except Exception:
        return None


_ATTRIBUTE_QUESTION_RE = re.compile(
    r"\b(?:where|live|living|work|job|employ|name|called|old|age|born|birthday|moved|stay)\w*\b", re.IGNORECASE)


async def prev_assistant_question(user_id: str, session_id: str | None, user_message: str,
                                  db=None) -> str:
    """The assistant message that immediately preceded ``user_message`` in its session, when it
    is a QUESTION about a personal attribute ("Where do you live now?") - the context a short
    elliptical answer ("no, Perth now", "it's Alex") needs. Context only: assistant text is
    never evidence for a fact about the user (memory_authority.supports). "" when there is
    none / on any failure."""
    if not session_id or not user_message:
        return ""
    sql = """
        SELECT a.content FROM chat_messages a
        WHERE a.session_id = ? AND a.role = 'assistant'
          AND a.created_at::timestamptz < (
                SELECT max(u.created_at::timestamptz) FROM chat_messages u
                WHERE u.session_id = ? AND u.role = 'user' AND u.content = ?)
        ORDER BY a.created_at::timestamptz DESC LIMIT 1
    """
    try:
        from db_pool import get_db_ctx  # type: ignore[import]

        async with get_db_ctx() as _db:
            row = await (await _db.execute(sql, (session_id, session_id, user_message))).fetchone()
        q = str(row[0] or "").strip() if row else ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("prev_assistant_question failed: %s", type(exc).__name__)
        return ""
    return q[-300:] if q.endswith("?") and _ATTRIBUTE_QUESTION_RE.search(q) else ""


async def latest_user_turn(user_id: str, *, within_minutes: int = 10, db=None) -> str:
    """The member's most recent user turn (any session) within ``within_minutes`` - the turn an
    explicit "remember ..." tool call belongs to. "" on none / failure."""
    sql = """
        SELECT cm.content FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id
        WHERE """ + _message_owner_expr() + """ = ? AND cm.role = 'user'
          AND cm.created_at::timestamptz >= (now()::timestamptz - make_interval(mins => ?::int))
        ORDER BY cm.created_at::timestamptz DESC LIMIT 1
    """
    try:
        from db_pool import get_db_ctx  # type: ignore[import]

        async with get_db_ctx() as _db:
            row = await (await _db.execute(sql, (user_id, within_minutes))).fetchone()
        return str(row[0] or "").strip() if row else ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("latest_user_turn failed: %s", type(exc).__name__)
        return ""


async def run_turn_digest(
    user_id: str,
    user_message: str,
    assistant_response: str = "",
    *,
    session_id: str | None = None,
    source: str = "turn_digest",
    speaker_verified: bool | None = None,
) -> dict:
    """LLM fact extraction on a single conversation exchange.

    Runs in the background after every chat/voice turn. Catches nuanced facts
    that regex patterns miss without waiting for the nightly batch digest.

    Returns a summary dict: {"new": N, "skipped_duplicates": N, "error": ...}
    """
    result: dict = {"user_id": user_id, "new": 0, "skipped_duplicates": 0, "skipped_low_quality": 0}

    # A pasted email / a system: line / another person's quoted speech is not the owner talking: the model reads
    # (and the facts are anchored to) the owner's own words only (own_words; ZMB I1/I2/I4).
    own = own_words.analyze(user_message)
    if own.changed:
        own_words.count_drops(source, own)
        user_message = own.text
        result["guard"] = list(own.reasons)

    prompt_text = ""
    if user_message and len(user_message.split()) < 4:
        # A SHORT answer to a personal-attribute question ("no, Perth now", "it's Alex") is a
        # fact only in the context of that question: read it with the question (context, never
        # evidence - memory_authority.supports needs the user's own words to carry the value).
        if memory_authority.enabled():
            prompt_text = await prev_assistant_question(user_id, session_id, user_message)
    if not user_message or (len(user_message.split()) < 4 and not prompt_text):
        return result
    # Skip purely procedural messages that can't contain personal facts.
    _skip_starts = ("what is", "what are", "how do", "explain", "tell me about",
                    "what time", "what's the", "search for", "play ", "set a timer",
                    "set timer", "remind me to", "add to my", "what's")
    msg_lower = user_message.lower().strip()
    if any(msg_lower.startswith(s) for s in _skip_starts):
        return result
    # Third-person pronoun subject ("she is allergic to nuts", "he's a doctor"):
    # this single-turn prompt has no antecedent context, so the LLM can only guess
    # who the fact is about — observed misattributing a friend's allergy to THE
    # USER ("The user is allergic to nuts"). The deterministic coreference path
    # (memory_extractor._pronoun_fact_candidates + session-history anchoring) owns
    # these turns; skip the context-free LLM digest rather than let it guess.
    # Possessive starts included ("her birthday is actually…" produced a guessed
    # "User's birthday is March 25." — QA review F2 evidence).
    if re.match(r"^(?:and\s+|oh[,\s]+|btw[,\s]+)?(?:she|he|they|her|his|their)\b", msg_lower):
        result["skipped_reason"] = "pronoun_subject_no_context"
        return result
    # A turn naming an entity the user asked Zoe to forget is not mined (and saves the model call).
    if not await _skip_forgotten_turns(user_id, [user_message], "turn_digest"):
        result["skipped_reason"] = "forgotten_entity"
        return result

    try:
        from memory_service import get_memory_service, MemoryServiceError  # type: ignore[import]
        svc = get_memory_service()

        # Numeric dates become words (household day-first order) before the model reads
        # them — its default is month-first ("7/8/1991" -> "July 8"). The stored
        # evidence excerpt below stays the user's verbatim words.
        from date_locale import normalize_numeric_dates
        prompt = _TURN_EXTRACTION_PROMPT.format(
            user_message=(f"(replying to Zoe's question: \"{prompt_text}\") " if prompt_text else "")
            + normalize_numeric_dates(user_message)[:600])
        payload = {
            "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
            "messages": [
                {"role": "system", "content": "You are a precise fact extractor. Return ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 256,
            "temperature": 0.1,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.debug("turn_digest: LLM call failed for %s: %s", user_id, exc)
            return result

        # Parse JSON array from response
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start == -1 or end == 0:
                return result
            facts = json.loads(raw[start:end])
            if not isinstance(facts, list):
                return result
        except (json.JSONDecodeError, ValueError):
            return result

        if not facts:
            return result

        # Light dedup: load existing facts as a text blob for word-overlap check
        try:
            from zoe_agent import _mempalace_load_user_facts  # type: ignore[import]
            existing_text = await _mempalace_load_user_facts(user_id, limit=50)
        except Exception:
            existing_text = ""

        import hashlib as _hashlib
        base_turn_id = _hashlib.sha1(user_message.encode("utf-8", "ignore")).hexdigest()[:16]
        # The digest model can flatten "I'm anxious about X" to "User has X".
        # Read the feeling from the user's OWN words and store it beside the
        # fact (metadata `affect`, stored as candidate_affect) so continuity can
        # still say how they felt.
        from memory_gate import extract_affect
        turn_affect, affect_sentence = extract_affect(user_message)
        # Verbatim evidence beside each distilled fact; MemoryService scrubs
        # and caps it at the write boundary.
        turn_excerpt = " ".join(user_message.split())
        # Implicit supersede (gap #4, flag-dark ZOE_MEMORY_IMPLICIT_SUPERSEDE): None
        # unless the flag is on AND the user's own words carry a change-of-state cue.
        change_cue = _implicit_change_cue(user_message)
        changed_refs: list = []
        fresh_candidates: list = []
        existing_rows = None  # approved rows, read once, only for a cue turn

        for idx, item in enumerate(facts):
            fact = (item.get("fact") or "").strip()
            if not fact or len(fact) < 8:
                continue
            fact_type = item.get("type", "fact")
            fact_tags = ["turn_digest", "auto_extract"]
            fact_changes = False
            if change_cue:
                from memory_supersede import STATE_CHANGE, fact_cue, is_tombstone
                fact_changes = fact_cue(fact) is not None
                if not fact_changes:
                    # A correction's new fact carries no cue word ("User's mum lives
                    # in Bendigo"); it is a change iff it replaces an approved row
                    # (same topic / exclusive home slot). Otherwise the overlap
                    # dedup below drops it as a duplicate of the row it retires.
                    if existing_rows is None:
                        from memory_supersede import SCAN_LIMIT
                        try:
                            existing_rows = await svc.list_by_status(
                                user_id=user_id, status="approved", limit=SCAN_LIMIT)
                        except Exception:
                            existing_rows = []
                    from memory_supersede import changes_existing
                    fact_changes = changes_existing(fact, existing_rows, change_cue)
                if is_tombstone(fact):
                    # "User dropped the half-marathon" records a change, never a
                    # current fact: the card and the recall packet treat it so.
                    fact_type = STATE_CHANGE
                    fact_tags = fact_tags + [STATE_CHANGE]
            # Word-overlap dedup (same as nightly digest). A change fact skips it: its
            # words are the OLD fact's plus "no longer", so it always "overlaps"
            # ("User no longer lives in Dunedin." scores 0.83 against "User lives in
            # Dunedin." and was dropped as a duplicate of the fact it retires).
            # Token-level, per stored fact (memory_overlap): a fact holding a NEW name / number /
            # date is never a duplicate, and one that extends a stored fact supersedes it below.
            verdict, _stored = dedup_verdict(fact, existing_text)
            if verdict == "duplicate" and not fact_changes:
                result["skipped_duplicates"] += 1
                _count_drop("turn_digest", "dedup_overlap", gate=False)
                continue
            if not _passes_quality_gate(fact):
                result["skipped_low_quality"] += 1
                logger.debug("run_turn_digest: dropped non-fact: %r", fact[:60])
                continue
            # Anchor validation: this single-turn LLM guesses "the user" as the
            # relationship anchor when the text doesn't say ("Emily is the wife"
            # → "Emily is the user's wife", live 2026-07-12). Only accept a
            # user-anchored relationship the turn supports ("my <role>").
            try:
                from memory_quality import user_relationship_claim_unsupported
                if user_relationship_claim_unsupported(fact, user_message):
                    result["skipped_low_quality"] += 1
                    _count_drop("turn_digest", "user_anchor_unsupported")
                    logger.info("run_turn_digest: dropped unsupported user-anchored relationship: %r", fact[:70])
                    continue
            except Exception:
                pass
            # Roles are stated, never guessed from a name (people_roles.py): "Casey is the
            # wife" is kept only when the user's own words put Casey and "wife" together.
            try:
                from people_roles import named_role_claim_unsupported
                if named_role_claim_unsupported(fact, user_message):
                    result["skipped_low_quality"] += 1
                    _count_drop("turn_digest", "role_claim_unsupported")
                    logger.info("run_turn_digest: dropped unstated role claim: %r", fact[:70])
                    continue
            except Exception:
                pass
            # Cross-writer reconciliation (QA review F9): the turn digest used
            # to blind-ADD, stacking its variant of a fact next to the memory-
            # expert and person_extractor copies. Shared ADD/UPDATE/SKIP
            # decision (entity-guarded); never raises — errors → ADD.
            try:
                from memory_quality import reconcile_for_ingest
                op, target_id = await reconcile_for_ingest(
                    svc, fact, user_id, extend_supersedes=True)
            except Exception:
                op, target_id = "add", None
            if op == "skip":
                result["skipped_duplicates"] += 1
                _count_drop("turn_digest", "dedup_reconcile", gate=False)
                logger.info("turn_digest: dedup-skip kept=%s cand=%r", target_id, fact[:60])
                continue
            if op == "update" and target_id:
                try:
                    fact_affect = _affect_for_fact(fact, turn_affect, affect_sentence, user_message)
                    new_ref = await svc.review(
                        target_id,
                        decision="edit",
                        edits=fact,
                        actor="turn_digest",
                        note="turn digest supersede (QA F9)",
                        # The updated row's feeling is THIS turn's, always: a
                        # feeling is carried onto a superseded neutral fact, and
                        # a neutral update clears the old one ("" overrides the
                        # value the edit would otherwise carry forward).
                        metadata={"affect": fact_affect},
                        source_excerpt=turn_excerpt,
                        # authority: the user's OWN turn is the anchor; the new row is THIS
                        # writer's (session/turn), never the superseded row's
                        anchor_text=user_message,
                        session_id=session_id,
                        turn_ref=f"{base_turn_id}-td{idx}",
                        prompt_text=prompt_text or None,
                        speaker_verified=speaker_verified,
                    )
                    if new_ref is not None:
                        result["new"] += 1
                        log_row("turn_digest", user_id, new_ref, "edited")
                        logger.info("turn_digest: superseded %s with %r", target_id, fact[:60])
                        if fact_changes:
                            changed_refs.append(new_ref)
                        continue
                except Exception as exc:
                    logger.warning("turn_digest: supersede failed (%s) — plain ingest", exc)
            try:
                fact_affect = _affect_for_fact(fact, turn_affect, affect_sentence, user_message)
                ref = await svc.ingest(
                    fact,
                    user_id=user_id,
                    source=source,
                    session_id=session_id,
                    user_turn_id=f"{base_turn_id}-td{idx}",
                    memory_type=fact_type,
                    confidence=0.82,
                    status="approved",
                    tags=fact_tags,
                    metadata={"affect": fact_affect} if fact_affect else None,
                    source_excerpt=turn_excerpt,
                    anchor_text=user_message,
                    prompt_text=prompt_text or None,
                    speaker_verified=speaker_verified,
                )
                if ref is not None and memory_authority.is_candidate(ref):
                    # held back (it disputes something the user said): ask ONE question
                    result["skipped_low_quality"] += 1
                    fresh_candidates.append(ref)
                    log_row("turn_digest", user_id, ref, "parked")
                elif ref is not None:
                    result["new"] += 1
                    log_row("turn_digest", user_id, ref, "stored")
                    logger.info("turn_digest: stored for %s: %s", user_id, fact[:80])
                    if fact_changes:
                        changed_refs.append(ref)
            except MemoryServiceError as exc:
                logger.debug("turn_digest: ingest failed for %s: %s", user_id, exc)

        if memory_authority.enabled() and session_id:
            import memory_disputes

            # a held-back write, or a topic the user just touched that has an open dispute,
            # becomes ONE question through the offer mechanism (never a lost row)
            result["dispute_questions"] = await memory_disputes.queue_questions(
                svc, user_id, session_id, user_message, fresh=fresh_candidates)

        if changed_refs:
            from memory_supersede import supersede_for_turn

            sup = await supersede_for_turn(svc, user_id, change_cue, changed_refs)
            result["superseded"] = sup["superseded"]
            if sup["superseded"]:
                # The card is otherwise rebuilt nightly; rebuild now so the same day's
                # card carries the replacement (no-op unless ZOE_USER_MODEL_BLOCK is on).
                from user_model_card import rebuild_user_model_card

                await rebuild_user_model_card(user_id)

    except Exception as exc:
        logger.warning("turn_digest: unexpected error for %s: %s", user_id, exc)
        result["error"] = str(exc)

    if result.get("new", 0) > 0:
        try:
            from zoe_agent import _invalidate_user_facts_cache
            _invalidate_user_facts_cache(user_id)
        except Exception:
            pass

    return result


async def run_memory_digest(user_id: str, db=None) -> dict:
    """Extract facts from today's chat history and write to MemPalace + memory_items.

    Args:
        user_id: The user to run the digest for.
        db:      asyncpg database connection (optional — opens its own if None).

    Returns:
        dict with keys: user_id, extracted, new, skipped_duplicates, error (if any).
    """
    result: dict = {
        "user_id": user_id,
        "extracted": 0,
        "new": 0,
        "skipped_duplicates": 0,
        "superseded": 0,
    }
    try:
        # The owner's verbatim turns of the last day or so, caught up into the exact-words index (idle work; the post-turn
        # hook indexes each turn as it is said, this closes any gap it left). Bounded, never raises.
        try:
            import exact_words
            await exact_words.backfill_recent(user_id)
        except Exception:  # noqa: BLE001
            pass
        chat_text = await _load_todays_messages(user_id, db)
        if not chat_text or len(chat_text.split()) < 20:
            logger.info("memory_digest: skipping %s — not enough chat activity today", user_id)
            result["skipped_reason"] = "insufficient_activity"
            return result

        from zoe_agent import _mempalace_load_user_facts  # type: ignore[import]
        from memory_service import MemoryServiceError, get_memory_service
        svc = get_memory_service()

        try:
            facts = await _extract_facts_with_gemma(chat_text)
        except ExtractorError as exc:
            # An outage is an ERROR row, never a quiet "no facts": the nightly
            # loop classifies it as extractor_errors. The EMOTIONAL pass below is
            # a separate call with its own parser and runs regardless — a
            # malformed fact reply must not cost the night's emotional moments
            # (Greptile P1 on #1682); both outcomes are recorded on the row.
            result["error"] = f"extractor_failed:{exc.kind}"
            logger.warning("memory_digest: extractor failed for %s: %s", user_id, exc)
            facts = []
        result["extracted"] = len(facts)

        if facts:
            existing_text = await _mempalace_load_user_facts(user_id, limit=100)
        else:
            existing_text = ""

        for item in facts:
            fact = (item.get("fact") or "").strip()
            if not fact or len(fact) < 10:
                continue
            # A fact about an entity the user asked Zoe to forget is dropped HERE, before the
            # contradiction pass below: that branch WRITES via review(edit), not ingest (ZMB F3).
            if not await _skip_forgotten_turns(user_id, [fact], "digest_fact"):
                _count_drop("digest", "forgotten_entity", gate=False)
                continue
            # Token-level, per stored fact (memory_overlap): never skips a fact that holds a new
            # name / number / date; one that extends a stored fact supersedes it at reconcile below.
            verdict, _stored = dedup_verdict(fact, existing_text)
            if verdict == "duplicate":
                logger.debug("memory_digest: dedup skip (duplicate of a stored fact): %s", fact[:60])
                result["skipped_duplicates"] += 1
                _count_drop("digest", "dedup_overlap", gate=False)
                continue

            # Anchor validation BEFORE the contradiction check: that branch can
            # WRITE via review(decision="edit") and would bypass a later gate. A
            # day-level transcript has no turn provenance, so drop EVERY
            # user-anchored relationship fact here — the per-turn digest (which
            # validates against the actual source turn) owns those.
            try:
                from memory_quality import user_relationship_claim_unsupported
                if user_relationship_claim_unsupported(fact, ""):
                    _count_drop("digest", "user_anchor_no_provenance")
                    logger.info("memory_digest: dropped user-anchored relationship (no turn provenance in nightly batch): %r", fact[:70])
                    continue
            except Exception:
                pass

            # The observation gate (ZMB K1 / K5): a model's reading of the day is stored only when the OWNER'S words
            # carry it. A fabricated link, a "you told me" nobody said and a guess about something said plainly used to
            # be stored approved and served; now they wait (pending, never served), are reworded, or are dropped.
            anchor_for_write = fact_anchor(item, chat_text) or ""
            gate_mode = memory_authority.observation_gate_mode()
            if gate_mode != "off":
                verdict = await _observation_verdict(svc, user_id, item, fact, chat_text)
                if verdict.kind != "supported" or verdict.text != fact:
                    logger.info("memory_digest: observation gate %s kind=%s reasons=%s basis=%s user=%s",
                                "ENFORCED" if gate_mode == "enforce" else "WOULD_HOLD",
                                verdict.kind, ",".join(verdict.reasons) or "-", verdict.basis or "-", user_id)
                if gate_mode == "enforce":
                    if verdict.kind == "restatement":
                        _count_drop("digest", "observation_restates_user")
                        result["observations_restated"] = result.get("observations_restated", 0) + 1
                        continue
                    if verdict.kind == "unsupported":
                        _count_drop("digest", "observation_unsupported")
                        if "attributed" in verdict.reasons:
                            _count_drop("digest", "observation_attributed_to_user")
                        result["observations_held"] = result.get("observations_held", 0) + 1
                        if _passes_quality_gate(verdict.text):
                            try:
                                # stored PENDING (never served) - or, when it disputes something the owner said, as the
                                # disputed candidate the authority wall always made of it (the owner is asked)
                                held = await svc.ingest(
                                    verdict.text, user_id=user_id, source="digest",
                                    memory_type=item.get("type", "fact"), confidence=0.5, status="approved",
                                    tags=["digest", item.get("type", "unknown"), "unsupported_observation"],
                                    anchor_text=anchor_for_write, hold="unsupported_observation")
                                log_row("digest", user_id, held, "held")
                                if held is not None and memory_authority.is_candidate(held):
                                    result["candidates"] = result.get("candidates", 0) + 1
                            except MemoryServiceError as exc:
                                logger.debug("memory_digest: held observation not stored: %s", exc)
                        continue
                    fact = verdict.text
                    if verdict.basis == memory_authority.CITED_BASIS:
                        anchor_for_write = verdict.anchor

            # which of the user's turns the fact came from (ZMB A3): its words and its chat_messages id
            excerpt, turn_id = locate_turn(item, fact, chat_text)

            # ── Contradiction check ──────────────────────────────────────
            # Pull the top-3 semantically similar existing facts and ask
            # the LLM whether any of them contradict the new one. If yes,
            # supersede the old memory via review(decision="edit"), which
            # writes the new fact and links it to the old row via
            # supersedes_id / superseded_by_id.
            superseded_any = False
            try:
                related = await svc.search(fact, user_id=user_id, limit=3, timeout_s=1.5)
            except Exception as exc:
                logger.debug("memory_digest: contradiction-search failed: %s", exc)
                related = []
            for candidate in related:
                existing_fact = (candidate.text or "").strip()
                if not existing_fact or existing_fact.lower() == fact.lower():
                    continue
                if not await _is_contradiction(fact, existing_fact):
                    continue
                try:
                    new_ref = await svc.review(
                        candidate.id,
                        decision="edit",
                        edits=fact,
                        actor="digest",
                        note="digest contradiction: superseded by newer turn",
                        # the day's USER turns (never assistant text): a verbatim user quote
                        # supports the fact, or the digest is an inference and cannot
                        # overrule what the user said
                        anchor_text=anchor_for_write,
                        source_excerpt=excerpt,
                        turn_ref=turn_id,
                    )
                except MemoryServiceError as exc:
                    logger.warning(
                        "memory_digest: supersede failed for %s: %s", user_id, exc
                    )
                    continue
                if new_ref is not None:
                    superseded_any = True
                    result["superseded"] += 1
                    log_row("digest", user_id, new_ref, "edited")
                    logger.info(
                        "memory_digest: superseded %s -> %s user=%s",
                        candidate.id, new_ref.id, user_id,
                    )
                    # A single supersede handles the new fact — skip the
                    # plain ingest below so we don't double-write.
                    break
            if superseded_any:
                continue

            tags = ["digest", item.get("type", "unknown")]
            if not _passes_quality_gate(fact):
                continue
            # Cross-writer reconciliation (QA review F9): the nightly digest's
            # contradiction pass above only supersedes on detected
            # contradictions — plain re-statements of an already-stored fact
            # still blind-ADDed. Shared ADD/UPDATE/SKIP decision
            # (entity-guarded); never raises — errors → ADD.
            try:
                from memory_quality import reconcile_for_ingest
                op, target_id = await reconcile_for_ingest(
                    svc, fact, user_id, extend_supersedes=True)
            except Exception:
                op, target_id = "add", None
            if op == "skip":
                _count_drop("digest", "dedup_reconcile", gate=False)
                logger.info("memory_digest: dedup-skip kept=%s cand=%r", target_id, fact[:60])
                continue
            if op == "update" and target_id:
                try:
                    new_ref = await svc.review(
                        target_id,
                        decision="edit",
                        edits=fact,
                        actor="digest",
                        note="nightly digest supersede (QA F9)",
                        anchor_text=anchor_for_write,
                        source_excerpt=excerpt,
                        turn_ref=turn_id,
                    )
                    if new_ref is not None:
                        result["superseded"] += 1
                        log_row("digest", user_id, new_ref, "edited")
                        logger.info("memory_digest: superseded %s with %r", target_id, fact[:60])
                        continue
                except Exception as exc:
                    logger.warning("memory_digest: supersede failed (%s) — plain ingest", exc)
            try:
                ref = await svc.ingest(
                    fact,
                    user_id=user_id,
                    source="digest",
                    memory_type=item.get("type", "fact"),
                    confidence=0.8,
                    status="approved",
                    tags=tags,
                    anchor_text=anchor_for_write,
                    source_excerpt=excerpt,
                    user_turn_id=turn_id,
                )
            except MemoryServiceError as exc:
                logger.warning("memory_digest: ingest failed for %s: %s", user_id, exc)
                continue
            if ref is not None and memory_authority.is_candidate(ref):
                result["candidates"] = result.get("candidates", 0) + 1
                log_row("digest", user_id, ref, "parked")
            elif ref is not None:
                result["new"] += 1
                log_row("digest", user_id, ref, "stored")
                logger.info("memory_digest: stored for %s: %s", user_id, fact[:80])

        # ── Emotional memory pass ──────────────────────────────────────────
        # Runs after fact extraction — separate LLM call that looks for
        # emotionally significant moments rather than neutral facts.
        try:
            emotional_new = await _emotional_memory_pass(user_id, chat_text, svc)
            result["emotional_new"] = emotional_new
        except Exception as exc:
            result["emotional_error"] = f"{type(exc).__name__}: {exc}"
            logger.debug("memory_digest: emotional pass failed (non-fatal) user=%s: %s", user_id, exc)

    except Exception as exc:
        logger.error("memory_digest: failed for %s: %s", user_id, exc, exc_info=True)
        result["error"] = str(exc)
    return result


async def _emotional_memory_pass(user_id: str, chat_text: str, svc) -> int:
    """Extract emotionally significant moments from today's chat and store them.

    Returns the number of new emotional memories stored.
    """
    from memory_service import MemoryServiceError  # type: ignore[import]

    prompt = _EMOTIONAL_EXTRACTION_PROMPT.format(chat_text=chat_text[:3000])
    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [
            {"role": "system", "content": "You are an empathetic listener. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 256,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "[]").strip()
    except Exception as exc:
        logger.debug("emotional_pass: LLM call failed: %s", exc)
        return 0

    # Parse JSON
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return 0
        moments = json.loads(raw[start:end])
        if not isinstance(moments, list):
            return 0
    except (json.JSONDecodeError, ValueError):
        return 0

    stored = 0
    for item in moments:
        moment = (item.get("moment") or "").strip()
        emotion = (item.get("emotion") or "other").strip().lower()
        significance = int(item.get("significance", 1))
        if not moment or significance < 2:
            continue
        # Same no-turn-provenance rule as the nightly fact loop: a day-level
        # moment like "User's wife was excited about the trip" can misattribute
        # a relationship the transcript never anchored — drop user-anchored
        # relationship phrasings here too.
        try:
            from memory_quality import user_relationship_claim_unsupported
            if user_relationship_claim_unsupported(moment, ""):
                logger.info("memory_digest: dropped user-anchored relationship in emotional moment: %r", moment[:70])
                continue
        except Exception:
            pass
        try:
            ref = await svc.ingest(
                moment,
                user_id=user_id,
                source="digest",
                memory_type="emotional_moment",
                confidence=0.9,
                status="approved",
                tags=["emotional", emotion],
            )
            if ref is not None:
                stored += 1
                log_row("emotional", user_id, ref, "stored")
                logger.info("emotional_pass: stored user=%s [%s] %s", user_id, emotion, moment[:60])
        except MemoryServiceError as exc:
            logger.debug("emotional_pass: ingest failed: %s", exc)
    return stored


class Transcript(str):
    """The day's user turns joined by newlines (a plain ``str`` everywhere it is used as one), that also
    remembers which ``chat_messages`` row each turn came from: ``turns`` = ``((message_id, content), ...)``.
    A loader that has no ids (a test double, the bench lab) returns a bare ``str`` and ``locate_turn`` falls back
    to a content-addressed id."""
    turns: tuple = ()

    def __new__(cls, text: str = "", turns=()):
        obj = super().__new__(cls, text)
        obj.turns = tuple(turns)
        return obj


async def _load_todays_messages(user_id: str, db=None) -> str:
    """Load today's user-turn messages using per-message metadata ownership."""
    owner_expr = _message_owner_expr()
    sql = """
            SELECT cm.content, cm.id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE """ + owner_expr + """ = ?
              AND cm.role = 'user'
              -- The ::text / ::timestamptz casts are required so the asyncpg
              -- positional-compat layer resolves timezone(text, timestamptz);
              -- without them the query errors and silently drops every message.
              -- (No literal question marks in this SQL — the compat layer would
              -- miscount them as bind placeholders.)
              AND cm.created_at::timestamptz >=
                  (now()::timestamptz - make_interval(hours => ?::int))
            ORDER BY cm.created_at ASC
            LIMIT 200
            """
    params = (user_id, _DIGEST_LOOKBACK_HOURS)
    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        if db is not None:
            rows = await (await db.execute(sql, params)).fetchall()
        else:
            # Self-acquire via the context manager when no connection is passed.
            # The bare `async for db in get_db(): break` form leaves the generator
            # suspended, so the connection is closed mid-query — which would make
            # every per-user digest skip after listing (Greptile P1 on #860).
            async with get_db_ctx() as _db:
                rows = await (await _db.execute(sql, params)).fetchall()
        if not rows:
            return ""
        pairs = [(row[0], (str(row[1]) if len(row) > 1 and row[1] is not None else ""))
                 for row in rows if row[0]]
        # pasted / third-person text is not the owner's (ZMB I1/I2): each turn is cut to the owner's own words
        # (or dropped) BEFORE the forgotten-turn skip, keeping the message id beside what is left of it
        owned = []
        for content, mid in pairs:
            kept = own_words.filter_turns([content], "digest")
            if kept:
                owned.append((kept[0], mid))
        pairs = owned
        lines = await _skip_forgotten_turns(user_id, [c for c, _ in pairs], "digest")
        # ``lines`` is a subsequence of the contents, in order: walk both to keep each kept turn's message id
        turns, i = [], 0
        for line in lines:
            while i < len(pairs) and pairs[i][0] != line:
                i += 1
            if i < len(pairs):
                turns.append((pairs[i][1], line))
                i += 1
        return Transcript("\n".join(lines), turns)
    except Exception as exc:
        logger.warning("memory_digest: could not load messages for %s: %s", user_id, exc)
        return ""


async def _skip_forgotten_turns(user_id: str, turns: list, reader: str) -> list:
    """``turns`` without the ones that name an entity the user asked Zoe to forget (the durable
    ``memory_forgotten`` ledger). The chat rows are not erased, so without this every transcript reader
    (nightly digest, idle consolidation, open loops) re-mines the forgotten turns once the 300 s tombstone
    has expired and the name comes back (ZMB F3). Skipped, not re-mined; logs a COUNT only. Fail-open: a
    ledger failure keeps the turns (the ingest chokepoint is the second wall)."""
    try:
        import memory_forgotten
        kept, dropped = await memory_forgotten.keep_unforgotten(user_id, turns)
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_digest: forgotten-turn filter unavailable (%s)", type(exc).__name__)
        return turns
    if dropped:
        logger.info("memory_digest: %s skipped %d turn(s) naming a forgotten entity user=%s",
                    reader, dropped, user_id)
    return kept


async def _cited_user_row_texts(svc, user_id: str, ids) -> list:
    """The texts of the approved USER-class rows (the owner's own statements) an observation cites in
    ``source_memory_ids``: this owner's, approved, rank >= ``user_stated``. A row that is not the owner's, not
    approved, or a model's paraphrase supports nothing. Bounded (5 ids); a lookup failure cites nothing."""
    out: list = []
    if not isinstance(ids, (list, tuple)):
        return out
    for rid in list(ids)[:5]:
        try:
            ref = await svc.get(str(rid))
        except Exception:  # noqa: BLE001
            continue
        if ref is None:
            continue
        md = ref.metadata or {}
        if str(md.get("user_id") or md.get("wing") or "") != user_id:
            continue
        if str(md.get("status") or "").strip().lower() != "approved":
            continue
        if memory_authority.row_rank(md, ref.text) < memory_authority.USER_RANK:
            continue
        out.append(ref.text)
    return out


async def _observation_verdict(svc, user_id: str, item: dict, fact: str, chat_text: str):
    """``memory_authority.check_observation`` for one extracted fact: the owner's words are the verbatim quote the
    model gave (``None`` = a quote that is not verbatim: no evidence) or the day's user turns, plus the approved
    user-class rows the item cites."""
    cited = await _cited_user_row_texts(svc, user_id, item.get("source_memory_ids")) if isinstance(item, dict) else []
    anchor = fact_anchor(item, chat_text)
    return memory_authority.check_observation(fact, None if anchor is None else str(anchor), cited_texts=cited)


def fact_anchor(item: dict, user_text: str) -> str | None:
    """The user's OWN words that anchor one extracted fact (memory_authority): the model's
    ``quote`` when it is a verbatim span of the user turns, ``None`` when it gave a quote that
    is NOT (a hallucinated span is no evidence), and the whole user transcript when the model
    gave no ``quote`` field at all (an older reply shape; ``supports`` still demands the
    subject, value, attribute and first person in ONE user sentence)."""
    if not isinstance(item, dict) or "quote" not in item:
        return user_text
    squash = lambda t: re.sub(r"\s+", " ", str(t or "")).strip().lower()  # noqa: E731
    quote = squash(item.get("quote"))
    # ONE message at a time: the transcript joins user turns with newlines, and a quote stitched
    # across two turns ("My dog is Teddy. Rex is coming over.") is not something the user said.
    if quote and any(quote in squash(line) for line in str(user_text or "").split("\n")):
        return str(item.get("quote")).strip()
    return None


def locate_turn(item: dict, fact: str, user_text: str) -> tuple[str | None, str | None]:
    """``(source_excerpt, user_turn_id)`` of the user turn a transcript-mined fact came from (ZMB A3).

    The excerpt is the user's own verbatim words: the model's ``quote`` when it is a verbatim span of one turn
    (``fact_anchor``), else the sentence that entails the fact (``memory_authority.supporting_span``). The id is
    the ``chat_messages`` id of the turn that holds it when the loader recorded it (``Transcript.turns``), else
    ``None`` and ``MemoryService.ingest`` stamps a content-addressed id of the excerpt. ``(None, None)`` when no
    user turn holds the words: a fact no user sentence backs has no turn to point at."""
    anchor = fact_anchor(item, user_text)
    if anchor is None:
        return None, None
    quoted = isinstance(item, dict) and "quote" in item
    span = str(anchor).strip() if quoted else memory_authority.supporting_span(fact, user_text)
    if not span:
        return None, None
    squash = lambda t: re.sub(r"\s+", " ", str(t or "")).strip().lower()  # noqa: E731
    want = squash(span)
    for message_id, content in getattr(user_text, "turns", ()) or ():
        if want in squash(content):
            return span, (message_id or None)
    return span, None


class ExtractorError(RuntimeError):
    """The fact extractor could not produce an answer (transport / HTTP / JSON).

    Distinct from "the transcript had no facts" (``[]``): an outage MUST
    classify as an error row in the nightly digest, not as ``attempted_no_facts``
    (Codex P2 on #1682). ``kind`` is the underlying exception class name.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


async def _extract_facts_with_gemma(chat_text: str) -> list[dict]:
    """Send chat transcript to the LLM and parse the JSON fact list.

    Returns ``[]`` only when the model answered and stated no facts. Every
    failure to get a parseable answer raises :class:`ExtractorError` so callers
    can tell an outage from an empty transcript (``memory_idle_consolidation``
    already catches it and leaves its watermark un-advanced).
    """
    if len(chat_text) > 3000:
        logger.warning(
            "memory_digest: transcript truncated to 3000 chars for fact "
            "extraction; dropped %d tail chars (may lose late-conversation facts)",
            len(chat_text) - 3000,
        )
    from date_locale import normalize_numeric_dates
    prompt = _EXTRACTION_PROMPT.format(chat_text=normalize_numeric_dates(chat_text)[:3000])
    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [
            {"role": "system", "content": "You are a precise fact extractor. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 512,
        "temperature": 0.1,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()
            text = raw["choices"][0]["message"]["content"].strip()
            # Extract JSON array (model may add preamble despite instructions)
            start = text.find("[")
            end = text.rfind("]") + 1
            if start == -1 or end == 0:
                logger.warning("memory_digest: LLM returned no JSON array: %s", text[:200])
                raise ExtractorError("NoJSONArray", text[:200])
            return json.loads(text[start:end])
    except ExtractorError:
        raise
    except json.JSONDecodeError as je:
        logger.warning("memory_digest: JSON parse error: %s", je)
        raise ExtractorError(type(je).__name__, str(je)) from je
    except Exception as exc:
        logger.warning("memory_digest: LLM call failed: %s", exc)
        raise ExtractorError(type(exc).__name__, str(exc)) from exc


async def _is_contradiction(new_fact: str, existing_fact: str) -> bool:
    """Ask the LLM whether a new fact contradicts an existing one.

    Fails **closed** (returns False) on any error — we prefer a
    duplicate over losing a real fact to a flaky LLM call.
    """
    if not new_fact or not existing_fact:
        return False
    prompt = _CONTRADICTION_PROMPT.format(
        new_fact=new_fact.strip(),
        existing_fact=existing_fact.strip(),
    )
    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [
            {"role": "system", "content": "You are a strict fact-contradiction judge. Return ONLY the JSON object."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 80,
        "temperature": 0.0,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return False
        parsed = json.loads(text[start:end])
        return bool(parsed.get("contradicts"))
    except Exception as exc:
        logger.debug("memory_digest: contradiction judge failed: %s", exc)
        return False


# ── Weekly consolidation ─────────────────────────────────────────────────────
#
# Runs once per week (default: Sunday 04:00). Goals:
#   1. **Merge near-duplicates**: memories whose text overlap ≥ 0.85
#      are clustered; the survivor is the RICHEST row (most distinct
#      entity / number / date tokens), then the higher authority class,
#      then the newest. Only an IDENTICAL duplicate is archived
#      (`archive_duplicate`); a near-duplicate with different text is left
#      alone. No LLM call needed for this step.
#   2. **Resolve contradictions**: for each pair in the top-K most similar
#      approved rows, ask the LLM if they contradict; if yes, keep the
#      newest and supersede the other.
#   3. **Soft-archive low-score stale rows** via
#      `MemoryService.sweep_soft_archive()`.
#
# The pass is idempotent: running it twice in a row is a no-op because
# merged / superseded rows already have ``status != 'approved'`` and are
# excluded from subsequent scans.


from memory_overlap import tokens as _overlap_tokens  # noqa: E402


def _text_overlap(a: str, b: str) -> float:
    """Containment overlap — symmetric inter/min(|A|,|B|).

    Jaccard penalises one-sided paraphrases ("user loves italian cuisine"
    vs "the user really loves italian cuisine" is only 0.67) even when
    the shorter sentence is fully covered by the longer one. For
    duplicate-detection we want the stronger "is one a subset of the
    other" signal, so we divide by min(|A|,|B|). Filler words (len ≤ 2)
    are ignored to keep stopwords from inflating similarity.
    """
    # word-boundary tokens: "kids." and "kids" are the same word (punctuation used to split them)
    wa = {w for w in _overlap_tokens(a) if len(w) > 2}
    wb = {w for w in _overlap_tokens(b) if len(w) > 2}
    if not wa or not wb:
        return 0.0
    inter = wa & wb
    return len(inter) / max(min(len(wa), len(wb)), 1)


async def _merge_near_duplicates(svc, user_id: str) -> int:
    """Collapse near-duplicate approved rows. Returns merge count."""
    approved = await svc.list_by_status(
        user_id=user_id, status="approved", limit=10_000
    )
    if len(approved) < 2:
        return 0
    # Pin the survivor of each cluster as the keeper. RICHNESS first: the row with more distinct
    # entity / number / date tokens ("... two kids Mika and Biscuit" beats "... two kids") must
    # never be the one retired in favour of a sparser echo. Ties: the row with the higher authority
    # class (a row the user said outranks a model's), then the newest.
    approved.sort(key=lambda r: str(r.metadata.get("added_at", "") or ""), reverse=True)  # newest first
    approved.sort(  # stable: keeps the newest-first order inside equal (richness, rank)
        key=lambda r: (-richness(r.text or ""), -memory_authority.row_rank(r.metadata, r.text)))
    keepers: list = []
    merged = 0
    for ref in approved:
        text = (ref.text or "").strip()
        if not text:
            continue
        matched = False
        for keeper in keepers:
            if _text_overlap(text, keeper.text) >= 0.85:
                # An IDENTICAL duplicate is archived (nothing is lost, nothing is rewritten:
                # the old merge edited the weaker row into the keeper's text - 6,585 of 6,605
                # audit edits were such no-op rewrites stamped reviewed_by=consolidation). A
                # near-duplicate whose text DIFFERS is a different statement and is left alone.
                try:
                    if await svc.archive_duplicate(
                        ref.id, keeper.id, actor="consolidation",
                        note="weekly: identical duplicate of " + keeper.id,
                    ):
                        merged += 1
                except Exception as exc:
                    logger.debug(
                        "consolidation: merge skipped id=%s: %s", ref.id, exc
                    )
                matched = True
                break
        if not matched:
            keepers.append(ref)
    return merged


async def _resolve_contradictions(svc, user_id: str, max_pairs: int = 50) -> int:
    """Walk pairs of high-similarity approved rows; supersede older if contradicted."""
    approved = await svc.list_by_status(
        user_id=user_id, status="approved", limit=200
    )
    if len(approved) < 2:
        return 0
    resolved = 0
    pairs_checked = 0
    # Sort newest-first so that on contradiction we can always supersede
    # the older row and keep the newer one.
    approved.sort(key=lambda r: r.metadata.get("added_at", ""), reverse=True)
    for i, newer in enumerate(approved):
        # Re-read newer's status in case an earlier iteration superseded
        # it already.
        refreshed = await svc.get(newer.id)
        if refreshed is None or refreshed.metadata.get("status") != "approved":
            continue
        # Only compare against older rows (higher indices) that share
        # meaningful lexical overlap — cheap filter to avoid N² LLM calls.
        for older in approved[i + 1 :]:
            if pairs_checked >= max_pairs:
                return resolved
            if _text_overlap(newer.text, older.text) < 0.25:
                continue
            # Two different named people's facts never contradict each other, whatever the judge says
            # ("Dana lives in Hobart" does not retire "Leo lives in Perth"; bake-off verification X1).
            from memory_supersede import facts_compatible
            if not facts_compatible(newer.text, older.text):
                continue
            older_current = await svc.get(older.id)
            if older_current is None or older_current.metadata.get("status") != "approved":
                continue
            pairs_checked += 1
            if not await _is_contradiction(newer.text, older.text):
                continue
            try:
                if await svc.review(
                    older.id,
                    decision="edit",
                    edits=newer.text,
                    actor="consolidation",
                    note="weekly: contradicted by newer fact",
                ) is not None:       # None = refused by the opt-out wall
                    resolved += 1
            except Exception as exc:
                logger.debug(
                    "consolidation: supersede skipped id=%s: %s", older.id, exc
                )
    return resolved


async def _implicit_conflict_pass(user_id: str) -> dict | None:
    """Nightly implicit-conflict pass (gap #4; flag-dark ZOE_MEMORY_IMPLICIT_SUPERSEDE,
    None when off — no store read). The LLM contradiction passes above judge only
    pairs a search or a 0.25 overlap puts in front of them, and "User dropped the
    half-marathon" is not a contradiction of "training for a half-marathon" in their
    prompt's sense. This is deterministic (``memory_supersede.conflict_pairs``: a newer
    change cue on the same topic, or a different home), capped per user per run, and
    skips opted-out users like every automatic write."""
    import memory_supersede

    if not memory_supersede.enabled():
        return None
    from memory_service import _user_opted_out, get_memory_service

    if await _user_opted_out(user_id):
        return {"skipped": "opt_out"}
    return await memory_supersede.nightly_conflict_pass(get_memory_service(), user_id)


async def run_weekly_consolidation(user_id: str) -> dict:
    """Per-user Sunday pass: merge duplicates, resolve contradictions, soft-archive.

    Returns a summary dict safe to log or surface via the admin UI.
    Never raises: each step is wrapped so one failure doesn't abort
    downstream work.
    """
    from memory_service import get_memory_service
    svc = get_memory_service()
    summary = {
        "user_id": user_id,
        "merged": 0,
        "resolved_contradictions": 0,
        "archived": 0,
    }
    try:
        summary["merged"] = await _merge_near_duplicates(svc, user_id)
    except Exception as exc:
        logger.warning("consolidation: merge failed user=%s: %s", user_id, exc)
    try:
        summary["resolved_contradictions"] = await _resolve_contradictions(svc, user_id)
    except Exception as exc:
        logger.warning("consolidation: contradiction pass failed user=%s: %s", user_id, exc)
    try:
        import memory_disputes

        summary["stale_disputes"] = await memory_disputes.expire_stale(svc, user_id)
    except Exception as exc:
        logger.warning("consolidation: dispute expiry failed user=%s: %s", user_id, exc)
    try:
        archived_ids = await svc.sweep_soft_archive(user_id=user_id, actor="decay_sweep")
        summary["archived"] = len(archived_ids)
    except Exception as exc:
        logger.warning("consolidation: sweep failed user=%s: %s", user_id, exc)
    logger.info("consolidation: %s", summary)
    return summary


async def _list_user_ids(sql: str, params: tuple = (), *, db=None) -> list[str]:
    """List user ids for a batch pass (`*_for_all` listing step).

    Uses the supplied connection when given, else a short-lived pooled acquire
    via ``get_db_ctx()`` for the listing only. Never use the bare
    ``async for db in get_db(): break`` form — it leaves the generator
    suspended at the yield, so the connection is closed out from under the
    query ("connection was closed in the middle of operation"). Materialize
    the rows before release.
    """
    from db_pool import get_db_ctx  # type: ignore[import]
    if db is not None:
        rows = await (await db.execute(sql, params)).fetchall()
    else:
        async with get_db_ctx() as _db:
            rows = await (await _db.execute(sql, params)).fetchall()
    return [row[0] for row in rows if row[0]]


async def run_weekly_consolidation_for_all(db=None) -> list[dict]:
    """Weekly consolidation for every chat-turn owner minus synthetic ids.

    (MemoryService has no ``list_users``; the old call was dead code whose
    AttributeError fallback — this query — was what always ran.)
    """
    try:
        user_ids = await _list_user_ids(
            _message_owner_users_sql(today_only=False), db=db
        )
    except Exception as exc:
        logger.error("consolidation: could not list users: %s", exc)
        return []
    user_ids = drop_synthetic_users(user_ids, pass_name="consolidation", log=logger)
    results = []
    for uid in user_ids:
        results.append(await run_weekly_consolidation(uid))
    return results


# The probe's "there has never been an owned user turn" answer. A fresh install
# is IDLE (nothing to digest), not "unknown": ``None`` is reserved for a probe
# FAILURE, which keeps the legacy every-zero-run-counts alerting. Infinity
# compares naturally against the lookback (never inside the window).
NO_OWNED_TURNS = float("inf")


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


async def newest_owned_user_turn_age_hours(db=None, cutoff=None) -> float | None:
    """Hours (at ``cutoff``) since the newest user turn owned by a real user.

    The nightly loop's INDEPENDENT answer to "was there anything to digest".
    Deliberately NOT the selection query (no lookback clause) so a broken
    window — the #1480 dead-window class — shows up as "turns exist but nobody
    was selected" instead of being invisible. Only turns strictly before
    ``cutoff`` count, so the caller can bind the same instant here and to
    selection (``run_nightly_digest_pass``).

    Returns ``NO_OWNED_TURNS`` (``inf``) when no owned turn has ever been
    written — a fresh install is idle, not unknown — and ``None`` ONLY when the
    probe itself failed. Never raises.
    """
    if cutoff is None:
        cutoff = _utcnow()
    owner_expr = _message_owner_expr()
    sql = f"""
        SELECT EXTRACT(EPOCH FROM (?::timestamptz - max(cm.created_at::timestamptz))) / 3600.0
        FROM chat_messages cm
        JOIN chat_sessions cs ON cm.session_id = cs.id
        WHERE cm.role = 'user'
          AND ({owner_expr}) IS NOT NULL
          AND cm.created_at::timestamptz < ?::timestamptz
    """
    params = (cutoff, cutoff)
    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        if db is not None:
            row = await (await db.execute(sql, params)).fetchone()
        else:
            async with get_db_ctx() as _db:
                row = await (await _db.execute(sql, params)).fetchone()
        if not row or row[0] is None:
            return NO_OWNED_TURNS
        return float(row[0])
    except Exception as exc:
        logger.warning("memory_digest: newest-turn probe failed (non-fatal): %s", exc)
        return None


def digest_input_seen(newest_turn_age_hours: float | None) -> bool | None:
    """Was there an owned user turn inside the digest lookback window.

    ``None`` only when the probe FAILED — callers then keep the legacy
    every-zero-run-counts alerting rather than guessing. ``NO_OWNED_TURNS``
    (fresh install) is simply outside the window: ``False``, i.e. idle.
    """
    if newest_turn_age_hours is None:
        return None
    return newest_turn_age_hours <= _DIGEST_LOOKBACK_HOURS


async def run_digest_for_all_active_users(db=None, cutoff=None) -> list[dict]:
    """Run memory digest for users active within the rolling lookback window.

    A rolling window, NOT calendar-today — see _message_owner_users_sql. Asking
    for "today" at 03:00 selected a three-hour dead window and processed nobody.
    With ``cutoff`` the window ends at that instant instead of ``now()`` (the
    nightly pass binds the same instant to the input probe).
    """
    results = []
    try:
        if cutoff is not None:
            sql = _message_owner_users_sql(
                today_only=False, lookback_hours=_DIGEST_LOOKBACK_HOURS, cutoff=True)
            params = (cutoff, _DIGEST_LOOKBACK_HOURS, cutoff)
        else:
            sql = _message_owner_users_sql(today_only=False, lookback_hours=_DIGEST_LOOKBACK_HOURS)
            params = (_DIGEST_LOOKBACK_HOURS,)
        user_ids = await _list_user_ids(sql, params, db=db)
    except Exception as exc:
        logger.error("memory_digest: could not list active users: %s", exc)
        return []

    for uid in user_ids:
        result = await run_memory_digest(uid, db=db)
        results.append(result)
        logger.info("memory_digest: %s", result)
    return results


async def run_nightly_digest_pass(db=None) -> dict:
    """Selection + per-user digest + input probe, all against ONE cutoff instant.

    Returns ``{"results", "cutoff", "newest_turn_age_hours", "input_seen"}``.
    A single cutoff computed before selection is bound to both queries, so a
    turn that lands while the pass runs is invisible to both — it cannot make
    the probe say "input exists" about a turn selection never had a chance to
    see. The probe never raises (``None`` = probe failure).
    """
    cutoff = _utcnow()
    results = await run_digest_for_all_active_users(db=db, cutoff=cutoff)
    age_h = await newest_owned_user_turn_age_hours(db=db, cutoff=cutoff)
    try:  # "N candidates rejected: reasons" — counts only, zero is logged too
        from memory_reject_ledger import log_nightly_summary
        log_nightly_summary(hours=24)
    except Exception:
        pass
    return {
        "results": results,
        "cutoff": cutoff,
        "newest_turn_age_hours": age_h,
        "input_seen": digest_input_seen(age_h),
    }


# ═══════════════════════════════════════════════════════════════════════════
# DREAMING MEMORY — arXiv:2604.20943
# Three-phase nightly/weekly memory reinforcement system:
#   Phase 1 (REM Reinforce)  — run nightly after fact extraction
#   Phase 2 (Deep Sleep)     — run weekly, promotes/archives pending memories
#   Phase 3 (Synthesis)      — run weekly, clusters and synthesizes patterns
# ═══════════════════════════════════════════════════════════════════════════

_CONCEPT_EXTRACTION_PROMPT = """\
Given the following memory fact, extract 1-5 concept tags (entity types or topics).
Return ONLY a JSON array of short lowercase strings, e.g. ["food", "preference", "location"].
Fact: {fact}
"""


async def _extract_concept_tags(fact: str) -> list[str]:
    """Use Gemma to extract concept tags from a fact. Returns [] on failure."""
    prompt = _CONCEPT_EXTRACTION_PROMPT.format(fact=fact[:300])
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_GEMMA_URL}/v1/chat/completions",
                json={
                    "model": os.environ.get("ZOE_LLM_MODEL", "gemma"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.1,
                },
                timeout=10.0,
            )
        text = resp.json()["choices"][0]["message"]["content"].strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            tags = json.loads(text[start:end])
            return [str(t).lower().strip()[:30] for t in tags if t][:5]
    except Exception as exc:
        logger.debug("concept extraction failed: %s", exc)
    return []


async def _rem_reinforce_pass(user_id: str) -> dict:
    """REM pass: for each new memory ingested tonight, strengthen related existing memories.

    Algorithm:
    1. Fetch tonight's new memories (added_at = today, consolidation_count = 0)
    2. For each, semantic search for top-5 neighbours
    3. Bump access_count on neighbours (new fact reinforces existing knowledge)
    4. Write related_ids on both the new memory and its neighbours
    5. Extract and store concept_tags if not already set
    """
    from memory_service import get_memory_service, leased_drawers
    import datetime, hashlib

    svc = get_memory_service()
    today = datetime.datetime.utcnow().date().isoformat()
    linked = 0
    tagged = 0

    try:
        col = leased_drawers(svc)   # per-call lease: never a handle across an await
        # ChromaDB $gte only supports int/float — filter by user_id only, then
        # post-filter by added_at date in Python (ISO strings compare correctly
        # lexicographically for same-length prefix matching).
        results = col.get(
            where={"user_id": {"$eq": user_id}},
            include=["documents", "metadatas"],
        )
        # Keep only memories added today (today = "YYYY-MM-DD")
        raw_ids   = results.get("ids")   or []
        raw_docs  = results.get("documents") or []
        raw_metas = results.get("metadatas") or []
        today_ids, today_docs, today_metas = [], [], []
        for _id, _doc, _meta in zip(raw_ids, raw_docs, raw_metas):
            at = (_meta or {}).get("added_at", "") or ""
            if at.startswith(today):
                today_ids.append(_id)
                today_docs.append(_doc)
                today_metas.append(_meta)
        ids   = today_ids
        docs  = today_docs
        metas = today_metas

        for mem_id, doc, meta in zip(ids, docs, metas):
            meta = dict(meta) if meta else {}

            # Skip if already processed
            if int(meta.get("consolidation_count", 0) or 0) > 0:
                continue

            # Extract concept tags if missing
            if not meta.get("concept_tags"):
                tags = await _extract_concept_tags(doc)
                if tags:
                    meta["concept_tags"] = ",".join(tags)
                    tagged += 1

            # Semantic search for neighbours
            neighbours = await svc.search(doc, user_id=user_id, limit=6)
            # Exclude self
            neighbour_ids = [n.id for n in neighbours if n.id != mem_id][:5]

            if neighbour_ids:
                # Bump access on neighbours (reinforcement)
                await svc.tick_access(user_id, neighbour_ids)

                # Write related_ids on new memory
                existing_related = set((meta.get("related_ids") or "").split(","))
                existing_related.update(neighbour_ids)
                meta["related_ids"] = ",".join(i for i in existing_related if i)
                linked += 1

                # Write related_ids on neighbours (bidirectional)
                nb_result = col.get(ids=neighbour_ids, include=["metadatas", "documents"])
                nb_ids = nb_result.get("ids") or []
                nb_docs = nb_result.get("documents") or []
                nb_metas = nb_result.get("metadatas") or []
                new_nb_metas = []
                for nm in nb_metas:
                    nm = dict(nm) if nm else {}
                    nb_related = set((nm.get("related_ids") or "").split(","))
                    nb_related.add(mem_id)
                    nm["related_ids"] = ",".join(i for i in nb_related if i)
                    new_nb_metas.append(nm)
                if nb_ids:
                    col.upsert(ids=nb_ids, documents=nb_docs, metadatas=new_nb_metas)

            # Mark as REM-processed
            meta["consolidation_count"] = int(meta.get("consolidation_count", 0) or 0) + 1
            col.upsert(ids=[mem_id], documents=[doc], metadatas=[meta])

    except Exception as exc:
        logger.warning("REM reinforce pass failed user=%s: %s", user_id, exc)

    summary = {"user_id": user_id, "linked": linked, "tagged": tagged}
    logger.info("dreaming/rem: %s", summary)
    return summary


def _promotion_score(meta: dict) -> float:
    """6-signal weighted promotion score for deep sleep gate.

    Returns a float in [0, 1]. Score >= 0.8 AND unique_query_count >= 3 → promote.
    """
    import datetime, math

    # Relevance proxy: confidence (0.30 weight)
    relevance = float(meta.get("confidence", 0.5) or 0.5)

    # Frequency: normalise access_count (0.24 weight) — cap at 50 for normalisation
    freq_raw = int(meta.get("access_count", 0) or 0)
    frequency = min(freq_raw / 50.0, 1.0)

    # Query diversity (0.15 weight) — cap at 10
    uqc = int(meta.get("unique_query_count", 0) or 0)
    diversity = min(uqc / 10.0, 1.0)

    # Recency: decay from last_accessed (0.15 weight)
    try:
        last = meta.get("last_accessed") or meta.get("added_at") or ""
        dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
        days_ago = (datetime.datetime.now(datetime.timezone.utc) - dt).days
        recency = math.exp(-days_ago / 30.0)  # e-folding 30 days
    except Exception:
        recency = 0.5

    # Consolidation depth (0.10 weight) — cap at 5
    consol = int(meta.get("consolidation_count", 0) or 0)
    consolidation = min(consol / 5.0, 1.0)

    # Conceptual richness (0.06 weight)
    tags = [t for t in (meta.get("concept_tags") or "").split(",") if t]
    richness = min(len(tags) / 5.0, 1.0)

    score = (
        0.30 * relevance
        + 0.24 * frequency
        + 0.15 * diversity
        + 0.15 * recency
        + 0.10 * consolidation
        + 0.06 * richness
    )
    return round(score, 4)


async def _deep_sleep_pass(user_id: str) -> dict:
    """Deep sleep pass: promote high-signal pending memories; archive stale ones.

    Runs once per week (Sunday nightly). Replaces the blunt auto-approve in
    run_weekly_consolidation with a 6-signal gate.

    Gate: score >= 0.8 AND unique_query_count >= 3 → pending → approved
    Stale: pending for 14+ days without qualifying → archived
    """
    from memory_service import get_memory_service, leased_drawers
    import datetime

    svc = get_memory_service()
    col = leased_drawers(svc)   # per-call lease: never a handle across an await
    promoted = 0
    archived = 0
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=14)).isoformat() + "Z"

    try:
        results = col.get(
            where={"$and": [{"user_id": {"$eq": user_id}}, {"status": {"$eq": "pending"}}]},
            include=["documents", "metadatas"],
        )
        ids = results.get("ids") or []
        docs = results.get("documents") or []
        metas = results.get("metadatas") or []

        for mem_id, doc, meta in zip(ids, docs, metas):
            meta = dict(meta) if meta else {}
            score = _promotion_score(meta)
            uqc = int(meta.get("unique_query_count", 0) or 0)
            added_at = meta.get("added_at") or ""

            if score >= 0.8 and uqc >= 3:
                meta["status"] = "approved"
                meta["consolidation_count"] = int(meta.get("consolidation_count", 0) or 0) + 1
                col.upsert(ids=[mem_id], documents=[doc], metadatas=[meta])
                promoted += 1
            elif added_at and added_at < cutoff:
                meta["status"] = "archived"
                meta["consolidation_count"] = int(meta.get("consolidation_count", 0) or 0) + 1
                col.upsert(ids=[mem_id], documents=[doc], metadatas=[meta])
                archived += 1

    except Exception as exc:
        logger.warning("deep sleep pass failed user=%s: %s", user_id, exc)

    summary = {"user_id": user_id, "promoted": promoted, "archived": archived}
    logger.info("dreaming/deep_sleep: %s", summary)
    return summary


_SYNTHESIS_PROMPT = """\
The following {n} memory facts all share the topic "{tag}".
Synthesize a single higher-order pattern or insight from them in one clear sentence.
Do NOT use names or personal identifiers. Output ONLY the synthesized fact, nothing else.

Facts:
{facts}
"""


# The synthesis prompt goes STRAIGHT to llama-server, whose single slot is
# --ctx-size 8192 (B6.6, scripts/setup/systemd/llama-server.service). Ten full
# stored documents (MemoryService.ingest() has no length limit) can exceed it,
# and the refused request silently skips the insight. So the prompt is budgeted
# to 2/3 of the slot (5461 tokens at 8192; the rest covers max_tokens=120, the
# chat template and estimator slack), counted fail-closed at chars/2.
_BRAIN_SLOT_TOKENS = int(os.environ.get("ZOE_BRAIN_SLOT_TOKENS", "8192") or 8192)
_SYNTHESIS_PROMPT_BUDGET_TOKENS = _BRAIN_SLOT_TOKENS * 2 // 3


def _build_synthesis_prompt(tag: str, sample: list[tuple[str, str]]) -> str:
    """Format _SYNTHESIS_PROMPT with every sampled doc, each capped to an equal
    share of the chars/2 budget left after the instruction text. Unchanged when
    the whole prompt already fits."""
    facts_text = "\n".join(f"- {doc}" for _, doc in sample)
    prompt = _SYNTHESIS_PROMPT.format(n=len(sample), tag=tag, facts=facts_text)
    budget_chars = _SYNTHESIS_PROMPT_BUDGET_TOKENS * 2
    if len(prompt) <= budget_chars or not sample:
        return prompt
    fixed = len(_SYNTHESIS_PROMPT.format(n=len(sample), tag=tag, facts=""))
    per_doc = max(40, (budget_chars - fixed) // len(sample) - 4)  # "- " + "\n" + "…"
    docs = [(doc if len(doc) <= per_doc else doc[:per_doc] + "…") for _, doc in sample]
    prompt = _SYNTHESIS_PROMPT.format(n=len(sample), tag=tag, facts="\n".join(f"- {d}" for d in docs))
    logger.info(
        "synthesis: prompt truncated to budget tag_len=%d docs=%d truncated=%d chars=%d budget_tokens=%d",
        len(tag), len(sample), sum(1 for _, d in sample if len(d) > per_doc), len(prompt),
        _SYNTHESIS_PROMPT_BUDGET_TOKENS,
    )
    return prompt

async def _synthesis_pass(user_id: str) -> dict:
    """Synthesis pass: cluster approved memories by concept tag; synthesize patterns.

    For clusters of 5+ memories sharing the same top concept tag, prompt Gemma
    to produce one higher-order insight. Stored with source="synthesis".
    """
    from memory_service import get_memory_service, MemoryServiceError, leased_drawers

    svc = get_memory_service()
    col = leased_drawers(svc)   # per-call lease: never a handle across an await
    synthesized = 0

    try:
        results = col.get(
            where={"$and": [
                {"user_id": {"$eq": user_id}},
                {"status": {"$eq": "approved"}},
                {"source": {"$ne": "synthesis"}},
            ]},
            include=["documents", "metadatas"],
        )
        ids = results.get("ids") or []
        docs = results.get("documents") or []
        metas = results.get("metadatas") or []

        # Build clusters by top concept tag
        from collections import defaultdict
        clusters: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for mem_id, doc, meta in zip(ids, docs, metas):
            meta = dict(meta) if meta else {}
            tags = [t.strip() for t in (meta.get("concept_tags") or "").split(",") if t.strip()]
            if tags:
                clusters[tags[0]].append((mem_id, doc))

        for tag, members in clusters.items():
            if len(members) < 5:
                continue
            # Take the 10 most relevant
            sample = members[:10]
            prompt = _build_synthesis_prompt(tag, sample)

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        f"{_GEMMA_URL}/v1/chat/completions",
                        json={
                            "model": os.environ.get("ZOE_LLM_MODEL", "gemma"),
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 120,
                            "temperature": 0.3,
                        },
                    )
                synthesis_text = resp.json()["choices"][0]["message"]["content"].strip()
                if len(synthesis_text) < 10:
                    continue
                # The synthesis LLM often returns meta-commentary ("The provided
                # facts illustrate…") instead of a stored-shaped insight — gate it.
                if not _passes_quality_gate(synthesis_text):
                    logger.debug("synthesis: dropped non-fact insight: %r", synthesis_text[:60])
                    continue

                ref = await svc.ingest(
                    synthesis_text,
                    user_id=user_id,
                    source="synthesis",
                    memory_type="insight",
                    confidence=0.85,
                    status="approved",
                    tags=[tag, "synthesis"],
                )
                if ref:
                    # Link synthesized memory back to source cluster
                    source_ids = [mid for mid, _ in sample]
                    col_result = col.get(ids=[ref.id], include=["metadatas", "documents"])
                    if col_result.get("ids"):
                        sm = dict((col_result["metadatas"] or [{}])[0])
                        sm["related_ids"] = ",".join(source_ids)
                        sm["concept_tags"] = tag
                        col.upsert(ids=[ref.id], documents=[synthesis_text], metadatas=[sm])
                    synthesized += 1
            except Exception as exc:
                logger.warning("synthesis failed for tag=%s user=%s: %s", tag, user_id, exc)

    except Exception as exc:
        logger.warning("synthesis pass failed user=%s: %s", user_id, exc)

    summary = {"user_id": user_id, "synthesized": synthesized}
    logger.info("dreaming/synthesis: %s", summary)
    return summary


# ── Open loops (nightly, dreaming phase 1.5) ─────────────────────────────────
#
# Consumers: proactive/triggers/morning_checkin.py (morning brief) and the
# legacy zoe_agent [OPEN LOOPS] block. Only expiry, the junk discard and (under
# ZOE_LOOP_LIFECYCLE) a supersede mark a loop resolved, so the extractor itself
# dedupes against the user's unresolved loops and ages out stale ones — otherwise the 48h window re-inserts the same loop every
# night and the brief (oldest first) repeats it forever.
_OPEN_LOOPS_MAX_PER_RUN = 5
_OPEN_LOOPS_TRANSCRIPT_CHARS = 3000   # same budget as the nightly fact extractor
_OPEN_LOOPS_DUP_OVERLAP = 0.6         # content-token containment ⇒ same loop
_OPEN_LOOPS_TTL_DAYS = 14             # unresolved loops older than this age out
_OPEN_LOOPS_MAX_FOLLOW_UP_DAYS = 14
# Counts-only log line; its own logger so the standalone dreaming runner can
# surface it without enabling memory_digest's INFO lines (those carry fact text).
_loops_log = logging.getLogger("memory_digest.open_loops")

_OPEN_LOOPS_PROMPT = """Identify open loops in these messages from the user — unresolved threads, worries, plans, things they are waiting on, or emotionally significant mentions that deserve a gentle follow-up later. Skip anything already resolved, plain questions, and requests Zoe handled on the spot (timers, reminders, music, lookups).

Messages (oldest first):
{messages}

Return ONLY a JSON array (at most 5 items, [] if none). Each item:
{{"loop_text": "one sentence about the user's open thread",
  "follow_up_hint": "what Zoe could gently ask or say later",
  "emotional_weight": 1-5,
  "follow_up_in_days": 0-14}}"""
# ZOE_LOOP_LIFECYCLE: the schema above gives only the range, so the model spreads its
# picks over it (3/5/7/10 days on the day-sim seeds, 1–7 on live rows) and a worry
# stated today waited most of a week. A caring friend checks in on a worry soon.
_OPEN_LOOPS_HORIZON = ("\nfollow_up_in_days is when a caring friend would naturally check in: "
                       "1 for a worry, a health concern or a strong feeling; the day after a "
                       "dated event; 2-3 for anything else; at most 7 unless it is a plan "
                       "months away.")
# A loop closed in the transcript window (a supersede, ZOE_LOOP_LIFECYCLE) must not be
# re-extracted from the very turns that created it: dedupe against those too.
_OPEN_LOOPS_RESOLVED_DEDUPE_DAYS = 2


def _parse_json_array(raw: str) -> list | None:
    """Parse the model's JSON array; None when there is no parseable array.

    Tolerates what the local 4B wraps around JSON: ```json fences, preamble,
    trailing prose (even prose containing brackets), and a one-key object
    wrapper ({"loops": [...]}).
    """
    text = re.sub(r"```(?:json)?", "", raw or "", flags=re.IGNORECASE).strip()
    decoder = json.JSONDecoder()
    for start in (m.start() for m in re.finditer(r"[\[{]", text)):
        try:
            value, _end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(value, dict):
            lists = [v for v in value.values() if isinstance(v, list)]
            return lists[0] if len(lists) == 1 else None
        if isinstance(value, list):
            return value
    return None


def _bounded_int(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(float(str(value).strip()))))
    except (TypeError, ValueError):
        return default


def _loop_is_dup(tokens: set[str], seen: list[set[str]]) -> bool:
    """Dedupe key: the loop's content tokens. A paraphrase of an unresolved loop
    (or of one accepted earlier this run) is the same loop once containment
    overlap reaches _OPEN_LOOPS_DUP_OVERLAP."""
    for other in seen:
        if tokens and other and len(tokens & other) / min(len(tokens), len(other)) >= _OPEN_LOOPS_DUP_OVERLAP:
            return True
    return False


async def _extract_open_loops(user_id: str, db=None) -> dict:
    """Extract open loops from the user's last 48h of turns into ``open_loops``.

    An open loop is an unresolved thread: a worry, a plan, something the user is
    waiting on, or something emotionally significant that deserves a follow-up.
    Calls the local brain the same way as the rest of this module (one non-
    streaming ``/v1/chat/completions`` POST; llama-server has one slot, so a
    busy brain queues the request and the timeout bounds the wait). Never
    raises; ``status`` says which stage stopped the pass.
    """
    from db_compat import get_compat_db as _get_compat_db
    created_at_valid_sql = CREATED_AT_VALID_TIMESTAMP_SQL.replace("created_at", "cm.created_at")
    from open_loop_lifecycle import lifecycle_enabled
    from open_loop_quality import loop_is_concrete, loop_turn_is_meta

    lifecycle = lifecycle_enabled()

    result = {"user_id": user_id, "extracted": 0, "inserted": 0, "skipped_dup": 0,
              "expired": 0, "discarded_meta": 0, "skipped_turns": 0, "status": "ok"}

    def _done(status: str) -> dict:
        result["status"] = status
        _loops_log.info(
            "OPEN_LOOPS user=%s extracted=%d inserted=%d skipped_dup=%d expired=%d "
            "discarded_meta=%d skipped_turns=%d status=%s",
            user_id, result["extracted"], result["inserted"], result["skipped_dup"],
            result["expired"], result["discarded_meta"], result["skipped_turns"], status,
        )
        return result

    # Age out first, every run — a quiet user's stale loop must not keep leading
    # the brief. Then load turns by the same owner rule as the user list
    # (per-turn metadata owner, then session owner).
    try:
        async with _get_compat_db() as _db:
            expired = await _db.execute(
                f"""UPDATE open_loops SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP
                   WHERE user_id = ? AND resolved IS NOT TRUE
                     AND created_at < CURRENT_TIMESTAMP - INTERVAL '{_OPEN_LOOPS_TTL_DAYS} days'""",
                (user_id,),
            )
            result["expired"] = int(getattr(expired, "rowcount", 0) or 0)
            # Junk already stored (no concrete anchor — open_loop_quality) is
            # discarded the only way the table allows: resolved, with resolved_at.
            async with _db.execute(
                "SELECT id, loop_text FROM open_loops WHERE user_id = ? AND resolved IS NOT TRUE",
                (user_id,),
            ) as cur:
                junk = [r[0] for r in await cur.fetchall() if not loop_is_concrete(str(r[1] or ""))]
            for loop_id in junk:
                await _db.execute(
                    "UPDATE open_loops SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?", (loop_id,),
                )
            result["discarded_meta"] = len(junk)
            async with _db.execute(
                f"""SELECT cm.content FROM chat_messages cm
                   JOIN chat_sessions cs ON cm.session_id = cs.id
                   WHERE {_message_owner_expr()} = ? AND cm.role = 'user'
                     AND CASE
                           WHEN {created_at_valid_sql}
                           THEN cm.created_at::timestamptz
                           ELSE NULL
                         END > CURRENT_TIMESTAMP - INTERVAL '2 days'
                   ORDER BY cm.created_at DESC LIMIT 50""",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("open_loops: message load failed user=%s: %s", user_id, exc)
        return _done("load_error")

    # A turn naming a forgotten entity is skipped, not re-mined (memory_forgotten; ZMB F3).
    try:
        import memory_forgotten
        rows, _dropped_forgotten = await memory_forgotten.keep_unforgotten(
            user_id, rows, text_of=lambda r: str(r[0] or ""))
        if _dropped_forgotten:
            logger.info("open_loops: skipped %d turn(s) naming a forgotten entity user=%s",
                        _dropped_forgotten, user_id)
    except Exception as exc:  # noqa: BLE001 - fail-open; the loop text is scrubbed again below
        logger.debug("open_loops: forgotten-turn filter unavailable (%s)", type(exc).__name__)

    # Newest turns win the budget; the prompt reads oldest first.
    lines: list[str] = []
    budget = _OPEN_LOOPS_TRANSCRIPT_CHARS
    for row in rows:
        text = " ".join(str(row[0] or "").split())
        if text and loop_turn_is_meta(text):  # Zoe's mechanics, not the user's life
            result["skipped_turns"] += 1
            continue
        line = "User: " + text
        if len(line) <= 6 or len(line) > budget:
            continue
        lines.append(line)
        budget -= len(line) + 1
    if not lines:
        return _done("no_messages")
    lines.reverse()

    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [
            {"role": "system", "content": "You extract open loops from conversations. Return ONLY a valid JSON array."},
            {"role": "user", "content": _OPEN_LOOPS_PROMPT.format(messages="\n".join(lines))
             + (_OPEN_LOOPS_HORIZON if lifecycle else "")},
        ],
        "max_tokens": 500,
        "temperature": 0.1,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"] or ""
    except Exception as exc:
        logger.warning("open_loops: LLM call failed user=%s: %s: %s", user_id, type(exc).__name__, exc)
        return _done("llm_error")

    parsed = _parse_json_array(raw)
    if parsed is None:
        logger.warning("open_loops: no JSON array in LLM reply user=%s (len=%d)", user_id, len(raw))
        return _done("parse_error")

    from memory_service import scrub_pii  # type: ignore[import]
    candidates: list[tuple[str, str, int, int]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        text, reject = scrub_pii(" ".join(str(item.get("loop_text") or "").split())[:300])
        hint, hint_reject = scrub_pii(" ".join(str(item.get("follow_up_hint") or "").split())[:200])
        if len(text) < 8 or reject:
            continue
        if not loop_is_concrete(text):
            result["discarded_meta"] += 1
            continue
        if not await _skip_forgotten_turns(user_id, [f"{text} {hint}"], "open_loops"):
            continue  # a loop about a forgotten entity is not stored
        candidates.append((
            text,
            "" if hint_reject else hint,
            _bounded_int(item.get("emotional_weight"), 1, 5, 1),
            # The model has no clock, so it gives a relative delay; the timestamp
            # is computed in SQL (the column is TIMESTAMP — a string bind fails).
            _bounded_int(item.get("follow_up_in_days"), 0, _OPEN_LOOPS_MAX_FOLLOW_UP_DAYS, 1),
        ))
        if len(candidates) >= _OPEN_LOOPS_MAX_PER_RUN:
            break
    result["extracted"] = len(candidates)
    if not candidates:
        return _done("ok")

    try:
        async with _get_compat_db() as _db:
            recent = (f" OR resolved_at > CURRENT_TIMESTAMP - INTERVAL "
                      f"'{_OPEN_LOOPS_RESOLVED_DEDUPE_DAYS} days'" if lifecycle else "")
            async with _db.execute(
                f"SELECT loop_text FROM open_loops WHERE user_id = ? AND (resolved IS NOT TRUE{recent})",
                (user_id,),
            ) as cur:
                seen = [_content_tokens(r[0]) for r in await cur.fetchall()]
            for text, hint, weight, days in candidates:
                tokens = _content_tokens(text)
                if _loop_is_dup(tokens, seen):
                    result["skipped_dup"] += 1
                    continue
                await _db.execute(
                    """INSERT INTO open_loops
                       (user_id, loop_text, follow_up_hint, emotional_weight, follow_up_after)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP + make_interval(days => ?::int))""",
                    (user_id, text, hint, weight, days),
                )
                result["inserted"] += 1
                seen.append(tokens)
    except Exception as exc:
        # Class + constraint only: a driver message can echo a bound value.
        logger.warning("open_loops: DB write failed user=%s: %s constraint=%s", user_id,
                       type(exc).__name__, getattr(exc, "constraint_name", "") or "-")
        return _done("db_error")

    return _done("ok")


async def run_dreaming_cycle(user_id: str, db=None, run_agent_sync_phase: bool = True) -> dict:
    """Run the full dreaming cycle for a user.

    Called by nightly-training-cycle.sh after run_memory_digest.
    Phase 1 (REM)         — runs nightly: reinforce recent memories
    Phase 1.5 (Open Loops)— runs nightly: extract unresolved threads same night they're mentioned
    Phase 1.7 (Conflicts) — runs nightly, flag-dark: implicit-conflict supersede (memory_supersede)
    Phase 2 (Deep Sleep)  — runs weekly (Sunday): consolidation
    Phase 3 (Synthesis)   — runs weekly (Sunday): long-term synthesis
    Phase 4 (Portrait)    — runs weekly (Sunday): synthesizes user portrait in SQLite
    Phase 5 (Agent Sync)  — runs weekly (Sunday): regenerate ZOE_SELF.md
    """
    import datetime

    is_sunday = datetime.datetime.utcnow().weekday() == 6
    result: dict = {"user_id": user_id}

    rem = await _rem_reinforce_pass(user_id)
    result["rem"] = rem

    # Idle person-link resolver (default OFF via ZOE_MEMORY_LINK_RESOLVER_ENABLED):
    # re-link person_pending facts to a real people.id once the contact exists.
    # A true no-op while the flag is off — no store scan, no DB open.
    try:
        link_result = await _resolve_pending_person_links(user_id, db=db)
        if link_result.get("scanned") or link_result.get("relinked"):
            result["link_resolver"] = link_result
    except Exception as exc:
        logger.warning("dreaming: link resolver failed user=%s: %s", user_id, exc)

    # Phase 1.5: Open loops extraction — runs nightly so loops are detected the same
    # day they're mentioned, not deferred until Sunday.
    try:
        loops_result = await _extract_open_loops(user_id, db=db)
        result["open_loops"] = loops_result
    except Exception as exc:
        logger.warning("dreaming: open_loops extraction failed user=%s: %s", user_id, exc)
        result["open_loops"] = {"status": "error", "error": str(exc)}

    # Phase 1.6: proactivity selector (ZOE_PROACTIVE_SELECTOR, default OFF — a
    # no-op without I/O when off). Ranks tonight's loops/moments/events.
    try:
        from proactive.selector import select_for_user

        selected = await select_for_user(user_id)
        if selected is not None:
            result["proactive_select"] = selected
    except Exception as exc:
        logger.warning("dreaming: proactive selector failed user=%s: %s", user_id, exc)

    # Phase 1.7: implicit-conflict pass (ZOE_MEMORY_IMPLICIT_SUPERSEDE, default OFF — a
    # no-op without I/O when off). Before the Sunday portrait and the
    # card rebuild, so both see its result.
    try:
        conflicts = await _implicit_conflict_pass(user_id)
        if conflicts is not None:
            result["implicit_conflicts"] = conflicts
    except Exception as exc:
        logger.warning("dreaming: implicit-conflict pass failed user=%s: %s", user_id, exc)

    if is_sunday:
        deep = await _deep_sleep_pass(user_id)
        result["deep_sleep"] = deep
        synth = await _synthesis_pass(user_id)
        result["synthesis"] = synth

        # Phase 4: Portrait synthesis — LLM-written narrative understanding of the user.
        # Stored in SQLite user_portraits and injected into every chat turn.
        try:
            from user_portrait import run_portrait_synthesis  # type: ignore[import]
            portrait = await run_portrait_synthesis(user_id, db=db)
            result["portrait"] = portrait
        except Exception as exc:
            logger.warning("dreaming: portrait synthesis failed user=%s: %s", user_id, exc)
            result["portrait"] = {"status": "error", "error": str(exc)}

        # Phase 5: Agent sync is system-wide, not per-user.  Callers that
        # iterate users should run it once for the first user only.
        if run_agent_sync_phase:
            try:
                from agent_sync import run_agent_sync  # type: ignore[import]
                sync_result = await run_agent_sync()
                result["agent_sync"] = sync_result
            except Exception as exc:
                logger.warning("dreaming: agent_sync failed: %s", exc)
                result["agent_sync"] = {"status": "error", "error": str(exc)}

    # Nightly: rebuild the user-model card (user_model_card.py; deterministic, no LLM) so
    # the always-present block tracks the store day by day. On Sundays the portrait
    # synthesis above has already rebuilt it. It is a no-op unless ZOE_USER_MODEL_BLOCK is on.
    if "card" not in (result.get("portrait") or {}):
        from user_model_card import rebuild_user_model_card  # type: ignore[import]

        result["user_model_card"] = await rebuild_user_model_card(user_id, db=db)

    # Opt-in, report-only Lint pass (default OFF via ZOE_MEMORY_LINT_IN_DREAMING).
    # Lint never mutates stored memory; it only emits a structured report of
    # contradictions / stale / orphan / duplicate rows for human review.
    try:
        from memory_lint import dreaming_lint_enabled, lint_user

        if dreaming_lint_enabled():
            report = await lint_user(user_id)
            result["lint"] = report.to_dict()
            logger.info(
                "dreaming: lint report user=%s scanned=%d findings=%d",
                user_id, report.scanned, report.total,
            )
    except Exception as exc:
        logger.warning("dreaming: lint pass failed user=%s: %s", user_id, exc)
        result["lint"] = {"status": "error", "error": str(exc)}

    logger.info("dreaming cycle complete: %s", result)
    return result


async def run_dreaming_for_all(db=None) -> list[dict]:
    """Dreaming cycle for every chat-turn owner minus synthetic ids (same user
    set as ``run_weekly_consolidation_for_all``)."""
    try:
        user_ids = await _list_user_ids(
            _message_owner_users_sql(today_only=False), db=db
        )
    except Exception as exc:
        logger.error("dreaming: could not list users: %s", exc)
        return []
    user_ids = drop_synthetic_users(user_ids, pass_name="dreaming", log=logger)

    results = []
    for idx, uid in enumerate(user_ids):
        r = await run_dreaming_cycle(uid, db=db, run_agent_sync_phase=(idx == 0))
        results.append(r)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# MUSIC TASTE DIGEST
# Nightly pass: reads raw music_listening_events from the Postgres pool, scores artists
# and genres by play/skip behaviour, and writes preference facts to MemPalace.
# ═══════════════════════════════════════════════════════════════════════════

async def run_music_taste_digest(user_id: str) -> dict:
    """Consolidate 30 days of music events into MemPalace preference memories.

    Scoring per entity (artist or genre):
        play       +2
        now_playing +1
        skip        -3
        skip_fast   -5
        repeat      +4
    Entities scoring > 3 → preference fact ingested into MemPalace.
    Entities scoring < -3 → avoidance fact ingested.

    Returns {"user_id": ..., "facts_ingested": N, "artists_tracked": M}.
    """
    import time as _time
    from collections import defaultdict

    result: dict = {"user_id": user_id, "facts_ingested": 0, "artists_tracked": 0}
    SIGNAL_WEIGHTS = {
        "complete":      +2,
        "repeat":        +3,
        "partial":       +1,
        "skip":          -2,
        "now_playing":   +1,
        "play":          +1,   # legacy fallback for old events
        "pause":          0,
        "volume_change":  0,
    }
    _SCORE_MAP = SIGNAL_WEIGHTS

    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        cutoff_ts = _time.time() - 30 * 86400

        # Short-lived pooled acquire for the read only. The bare
        # `async for db in get_db(): break` form leaves the generator
        # suspended at its yield, so the pooled connection is held for the
        # entire (substantial) scoring + MemPalace ingest work below instead
        # of being released after the query. Materialize the rows, then let
        # get_db_ctx release the connection before any scoring/ingest.
        async with get_db_ctx() as _db:
            events = await (
                await _db.execute(
                    """SELECT event_type, track_title, artist, genre
                       FROM music_listening_events
                       WHERE user_id = ? AND ts >= ?
                       ORDER BY ts ASC""",
                    (user_id, cutoff_ts),
                )
            ).fetchall()
    except Exception as exc:
        logger.warning("music_taste_digest: could not load events for %s: %s", user_id, exc)
        result["error"] = str(exc)
        return result

    if not events:
        logger.info("music_taste_digest: no events in last 30d for %s", user_id)
        result["skipped_reason"] = "no_events"
        return result

    # Accumulate scores per artist and genre
    artist_scores: dict[str, float] = defaultdict(float)
    artist_plays: dict[str, int] = defaultdict(int)
    artist_skips: dict[str, int] = defaultdict(int)
    genre_scores: dict[str, float] = defaultdict(float)
    genre_plays: dict[str, int] = defaultdict(int)
    genre_skips: dict[str, int] = defaultdict(int)

    for row in events:
        evt_type = row[0] or ""
        artist = (row[2] or "").strip()
        genre = (row[3] or "").strip()
        delta = _SCORE_MAP.get(evt_type, 0)

        if artist:
            artist_scores[artist] += delta
            if delta > 0:
                artist_plays[artist] += 1
            elif delta < 0:
                artist_skips[artist] += 1

        if genre:
            genre_scores[genre] += delta
            if delta > 0:
                genre_plays[genre] += 1
            elif delta < 0:
                genre_skips[genre] += 1

    result["artists_tracked"] = len(artist_scores) + len(genre_scores)

    # Build preference facts
    facts: list[tuple[str, str]] = []  # (fact_text, memory_type_hint)
    for artist, score in artist_scores.items():
        plays = artist_plays.get(artist, 0)
        skips = artist_skips.get(artist, 0)
        if score > 3:
            facts.append((
                f"User frequently plays {artist}: {plays} play(s), {skips} skip(s) in last 30 days.",
                "preference",
            ))
        elif score < -3:
            facts.append((
                f"User avoids {artist}: consistently skipped ({skips} skips, {plays} plays).",
                "preference",
            ))

    for genre, score in genre_scores.items():
        plays = genre_plays.get(genre, 0)
        skips = genre_skips.get(genre, 0)
        if score > 3:
            facts.append((
                f"User frequently listens to {genre} music: {plays} play(s), {skips} skip(s) in last 30 days.",
                "preference",
            ))
        elif score < -3:
            facts.append((
                f"User avoids {genre} music: consistently skipped ({skips} skips, {plays} plays).",
                "preference",
            ))

    if not facts:
        logger.info("music_taste_digest: no strong preferences found for %s", user_id)
        return result

    try:
        from memory_service import get_memory_service, MemoryServiceError  # type: ignore[import]
        svc = get_memory_service()
    except Exception as exc:
        logger.warning("music_taste_digest: memory service unavailable: %s", exc)
        result["error"] = str(exc)
        return result

    for fact_text, mem_type in facts:
        try:
            ref = await svc.ingest(
                fact_text,
                user_id=user_id,
                source="music_digest",
                memory_type="preference",
                confidence=0.85,
                status="approved",
                tags=["music", "taste", "auto"],
            )
            if ref is not None:
                result["facts_ingested"] += 1
                logger.info("music_taste_digest: stored for %s: %s", user_id, fact_text[:80])
        except Exception as exc:
            logger.warning("music_taste_digest: ingest failed for %s: %s", user_id, exc)

    logger.info("music_taste_digest: %s", result)
    return result


async def run_music_taste_digest_for_all(db=None) -> list[dict]:
    """Run music taste digest for all users who have any music events."""
    results = []
    try:
        user_ids = await _list_user_ids(
            "SELECT DISTINCT user_id FROM music_listening_events", db=db
        )
    except Exception as exc:
        logger.error("music_taste_digest: could not list users: %s", exc)
        return []
    user_ids = drop_synthetic_users(user_ids, pass_name="music_taste_digest", log=logger)

    for uid in user_ids:
        r = await run_music_taste_digest(uid)
        results.append(r)
        logger.info("music_taste_digest: %s", r)
    return results
