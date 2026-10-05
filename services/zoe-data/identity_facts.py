"""identity_facts — who the user IS comes from the ACCOUNT, never from memory.

Why this exists (live 2026-10-05): "whats my name" was answered with a full name
that belongs to nobody in the household. The row sat in the owner's palace as
``User's name is <X>`` and had been written by the NIGHTLY DIGEST, not by the user:
its Gemma fact-extractor read a day transcript that contained a speech-to-text
fragment naming a third person, asserted it as the user's own name, and its
contradiction pass then SUPERSEDED the genuine name fact with it
(``MemoryService.review(edit)`` carries the old row's ``source``/``session_id``
forward, which is why the polluted row looked like a regex/Telegram write). A
recall store is the wrong authority for "who am I": it is written by extractors
that mishear, mis-attribute and contradict each other. The account is not.

Three things live here, all pure or fail-soft (a turn is never broken):

1. ``identity_question_kind`` — the narrow set of own-identity questions ("what's my
   name", "who am I", "what do you call me", "where do I live", "what's my address",
   "what city am I in") and ``maybe_answer`` — the deterministic ONE-sentence reply
   built from the account. ``fast_tiers.resolve`` runs it BEFORE recall.
2. ``resolve_identity`` / ``identity_line`` — the same account facts as the single
   "You are talking to <name>, a member of this household in <place>." line the brain
   prompt carries (``zoe_flue_client``, ``ZOE_IDENTITY_BLOCK``).
3. ``is_user_name_assertion`` — the guard ``MemoryService`` applies so an AUTOMATIC
   writer can never again store (or supersede into) "the user's name is X".

Sources (first hit wins; NEVER a memory row):
    name   user_preferences.prefs["preferred_name"]   (the settings override)
           auth_users.settings JSON ``display_name`` / ``name``
           users.name                                  (title-cased when all-lowercase)
           auth_users.username
    home   weather_preferences (this user's city/country)
           system_preferences["weather_default_location"] (the household default)
           ZOE_LOCATION_CITY / ZOE_LOCATION_COUNTRY / ZOE_LOCATION_REGION env
    street user_preferences.prefs["home_address"]      (optional; absent → city only)

Only a REGISTERED account (an ``auth_users`` row) has an identity here: guests,
voice-guests and harness/demo ids resolve to ``None`` so nothing is invented for them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

KEY_PREFERRED_NAME = "preferred_name"
KEY_HOME_ADDRESS = "home_address"

# Writers that MINE facts from what was said (as opposed to the user dictating one, or an
# operator tool): ``user_prefs.MEMORY_OPT_OUT_SOURCES`` plus the voice-lane and idle-
# consolidation labels, which that opt-out list omits. None of them may assert the
# user's own name — that is an account fact.
AUTOMATIC_SOURCES = frozenset({
    "chat_regex", "turn_digest", "conversation", "ambient", "digest", "consolidation",
    "synthesis", "music_digest", "voice_regex", "voice_turn_digest", "idle_consolidation",
})

_NON_ACCOUNT_IDS = frozenset({"", "guest", "voice-guest", "default", "anonymous", "system"})

# ── account → Identity ────────────────────────────────────────────────────────

_COUNTRY_NAMES = {
    "AU": "Australia", "NZ": "New Zealand", "US": "United States", "GB": "United Kingdom",
    "UK": "United Kingdom", "CA": "Canada", "IE": "Ireland", "ZA": "South Africa",
    "SG": "Singapore", "IN": "India", "DE": "Germany", "FR": "France", "ES": "Spain",
    "IT": "Italy", "NL": "Netherlands", "JP": "Japan", "ID": "Indonesia", "MY": "Malaysia",
}

# An Australian IANA zone names its state/territory; a household default location
# stores a timezone but no region, so this is the one place a region is derivable.
_AU_ZONE_REGION = {
    "Australia/Perth": "Western Australia",
    "Australia/Eucla": "Western Australia",
    "Australia/Adelaide": "South Australia",
    "Australia/Darwin": "Northern Territory",
    "Australia/Brisbane": "Queensland",
    "Australia/Lindeman": "Queensland",
    "Australia/Sydney": "New South Wales",
    "Australia/Broken_Hill": "New South Wales",
    "Australia/Melbourne": "Victoria",
    "Australia/Hobart": "Tasmania",
    "Australia/Canberra": "Australian Capital Territory",
}


@dataclass(frozen=True)
class Identity:
    user_id: str
    name: str                      # what Zoe calls this person (override > account)
    account_name: str              # the account's own name (override ignored)
    has_preferred_name: bool = False
    city: str = ""
    region: str = ""
    country: str = ""
    street_address: str = ""

    @property
    def place(self) -> str:
        """``City, Region, Country`` — whichever parts exist, in that order."""
        return ", ".join(p for p in (self.city, self.region, self.country) if p)

    @property
    def city_region(self) -> str:
        return ", ".join(p for p in (self.city, self.region) if p)


def _is_account_id(user_id: str) -> bool:
    uid = (user_id or "").strip()
    return bool(uid) and uid.lower() not in _NON_ACCOUNT_IDS


def _display(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    return name.title() if name and name.islower() else name


def _country_name(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    return _COUNTRY_NAMES.get(v.upper(), v) if len(v) <= 2 else v


def _json_dict(raw: Any) -> dict:
    try:
        parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _one(db, sql: str, params: tuple) -> Optional[Any]:
    cur = await db.execute(sql, params)
    return await cur.fetchone()


def _col(row: Any, key: str, idx: int = 0) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        try:
            return row[idx]
        except Exception:  # noqa: BLE001
            return None


def build_identity(
    user_id: str,
    *,
    username: str = "",
    settings: Optional[dict] = None,
    users_name: str = "",
    prefs: Optional[dict] = None,
    city: str = "",
    country: str = "",
    sysloc: Optional[dict] = None,
) -> Optional[Identity]:
    """Pure: the Identity the account rows imply (no I/O, no memory). ``None`` when the
    account has no name at all. ``city``/``country`` are the user's own weather prefs;
    ``sysloc`` is the household default (``system_preferences.weather_default_location``)."""
    uid = (user_id or "").strip()
    settings, prefs, sysloc = settings or {}, prefs or {}, sysloc or {}
    account_name = _display(
        str(settings.get("display_name") or settings.get("name") or "").strip()
        or (users_name or "").strip()
        or (username or "").strip()
    )
    preferred = _display(str(prefs.get(KEY_PREFERRED_NAME) or "").strip())
    name = preferred or account_name
    if not name:
        return None

    city, country, region = (city or "").strip(), (country or "").strip(), ""
    sys_city = str(sysloc.get("city") or "").strip()
    if not city:
        city, country = sys_city, country or str(sysloc.get("country") or "").strip()
    if city and sys_city and city.lower() == sys_city.lower():
        # the household default: its region is its own, or its timezone's
        region = (str(sysloc.get("region") or sysloc.get("state") or sysloc.get("admin1") or "").strip()
                  or _AU_ZONE_REGION.get(str(sysloc.get("timezone") or ""), ""))
        country = country or str(sysloc.get("country") or "").strip()
    if not city:  # last resort: the same env fallback the weather router uses
        city = os.environ.get("ZOE_LOCATION_CITY", "").strip()
        region = region or os.environ.get("ZOE_LOCATION_REGION", "").strip()
        country = country or os.environ.get("ZOE_LOCATION_COUNTRY", "").strip()
        if not region:
            region = _AU_ZONE_REGION.get(os.environ.get("ZOE_TIMEZONE", "").strip(), "")
    return Identity(
        user_id=uid,
        name=name,
        account_name=account_name or name,
        has_preferred_name=bool(preferred),
        city=city,
        region=region if city else "",
        country=_country_name(country) if city else "",
        street_address=re.sub(r"\s+", " ", str(prefs.get(KEY_HOME_ADDRESS) or "").strip()),
    )


async def _load_identity(db, user_id: str) -> Optional[Identity]:
    uid = (user_id or "").strip()
    if not _is_account_id(uid):
        return None
    auth = await _one(db, "SELECT username, settings FROM auth_users WHERE user_id = ?", (uid,))
    if auth is None:  # not a registered account (harness / demo / stale id)
        return None
    users_row = await _one(db, "SELECT name FROM users WHERE id = ?", (uid,))
    prefs_row = await _one(db, "SELECT prefs FROM user_preferences WHERE user_id = ?", (uid,))
    wp = await _one(db, "SELECT city, country FROM weather_preferences WHERE user_id = ?", (uid,))
    sysrow = await _one(db, "SELECT value FROM system_preferences WHERE key = ?",
                        ("weather_default_location",))
    return build_identity(
        uid,
        username=str(_col(auth, "username", 0) or ""),
        settings=_json_dict(_col(auth, "settings", 1)),
        users_name=str(_col(users_row, "name", 0) or ""),
        prefs=_json_dict(_col(prefs_row, "prefs", 0)),
        city=str(_col(wp, "city", 0) or ""),
        country=str(_col(wp, "country", 1) or ""),
        sysloc=_json_dict(_col(sysrow, "value", 0)),
    )


_CACHE_TTL_S = 120.0
_cache: dict[str, tuple[float, Optional[Identity]]] = {}
_LOAD_TIMEOUT_S = 1.5
_FAIL_TTL_S = 15.0


def clear_cache(user_id: Optional[str] = None) -> None:
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)


async def resolve_identity(user_id: str, db=None) -> Optional[Identity]:
    """The account's identity, or ``None`` (guest / unregistered / DB trouble).
    Cached per user for ``_CACHE_TTL_S``; failures are never cached. NEVER raises."""
    uid = (user_id or "").strip()
    if not _is_account_id(uid):
        return None
    hit = _cache.get(uid)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    try:
        if db is not None:
            ident = await asyncio.wait_for(_load_identity(db, uid), _LOAD_TIMEOUT_S)
        else:
            from db_pool import get_db_ctx  # type: ignore[import]

            async def _go() -> Optional[Identity]:
                async with get_db_ctx() as conn:
                    return await _load_identity(conn, uid)

            ident = await asyncio.wait_for(_go(), _LOAD_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — identity must never break a turn
        logger.debug("identity_facts: resolve failed for %s: %r", uid, exc)
        # Remember the failure briefly so a hung DB costs ONE timeout per window, not one
        # per turn (the entry expires _FAIL_TTL_S after now).
        _cache[uid] = (now - _CACHE_TTL_S + _FAIL_TTL_S, None)
        return None
    _cache[uid] = (now, ident)
    return ident


# ── the brain-prompt line (ZOE_IDENTITY_BLOCK) ────────────────────────────────

def identity_block_enabled() -> bool:
    """``ZOE_IDENTITY_BLOCK`` — default ON.

    One short line, built only from facts the account already shows its owner (their
    own name and their household's location), and cached per user. It removes the
    "visitor" assumption ("How is your time in Australia going?") that the brain makes
    when nothing tells it who it is talking to or where the house is. Unset/0/false
    turns it off and the outbound bytes are identical to before."""
    from typed_env import env_bool

    return env_bool("ZOE_IDENTITY_BLOCK", True)


def identity_line(ident: Identity) -> str:
    where = f" in {ident.place}" if ident.city else ""
    return f"You are talking to {ident.name}, a member of this household{where}."


async def identity_block(user_id: str) -> str:
    """The prompt line for ``user_id`` or ``""`` (flag off / not an account). NEVER raises."""
    try:
        if not identity_block_enabled():
            return ""
        ident = await resolve_identity(user_id)
        return identity_line(ident) if ident is not None else ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("identity_facts: block skipped: %r", exc)
        return ""


# ── own-identity questions ────────────────────────────────────────────────────

_LEAD = r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe|please)[\s,]+)*"
_TAIL = r"(?:\s+(?:again|please|then|now|exactly|anyway|zoe|mate))*\W*$"


def _rx(body: str) -> re.Pattern:
    return re.compile(_LEAD + body + _TAIL, re.IGNORECASE)


_IDENTITY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("fullname", _rx(r"(?:what(?:['’]s|\s+is|\s+are)?|whats)\s+my\s+(?:full|last|sur|middle)\s*name")),
    ("name", _rx(r"(?:what(?:['’]s|\s+is|\s+are)?|whats)\s+my\s+(?:first\s+|real\s+)?name")),
    ("name", _rx(r"do\s+you\s+(?:know|remember)\s+my\s+name")),
    ("name", _rx(r"(?:can\s+you\s+|could\s+you\s+)?(?:tell|remind)\s+me\s+(?:what\s+)?my\s+(?:first\s+)?name(?:\s+is)?")),
    ("call", _rx(r"(?:what|who)\s+(?:do|did|should)\s+you\s+call\s+me")),
    ("call", _rx(r"what\s+am\s+i\s+called")),
    ("call", _rx(r"what\s+(?:name\s+)?(?:do|did)\s+you\s+have\s+(?:for|down\s+for)\s+me")),
    ("self", _rx(r"who\s+am\s+i")),
    ("home", _rx(r"where\s+(?:do|did)\s+(?:i|we)\s+live")),
    ("home", _rx(r"where(?:['’]s|\s+is)\s+(?:my|our)\s+(?:home|house|place)")),
    ("home", _rx(r"(?:which|what)\s+(?:city|town|suburb|state|country|area|region)\s+"
                 r"(?:am\s+i|are\s+we)\s+in")),
    ("home", _rx(r"(?:which|what)\s+(?:city|town|suburb|state|country|area|region)\s+"
                 r"(?:do|did)\s+(?:i|we)\s+live\s+in")),
    ("address", _rx(r"(?:what(?:['’]s|\s+is)|whats)\s+(?:my|our)\s+(?:home\s+|street\s+|house\s+)?address")),
    ("address", _rx(r"(?:what|which)\s+address\s+do\s+you\s+have\s+for\s+me")),
)


def identity_question_kind(message: str) -> str:
    """"name" / "fullname" / "call" / "self" / "home" / "address" for a question that
    asks WHO the user is or WHERE they live, else "". Whole-utterance anchored, so
    "who am I meeting tomorrow" and "what's my name for the booking" never match. Pure."""
    text = (message or "").strip()
    if not text or len(text) > 120:
        return ""
    for kind, rx in _IDENTITY_PATTERNS:
        if rx.match(text):
            return kind
    return ""


def reply_for(kind: str, ident: Identity) -> Optional[str]:
    """The one-sentence answer, or ``None`` when the account lacks the fact (the turn
    then falls through to the brain exactly as before)."""
    if kind in ("name",):
        return f"Your name is {ident.name}."
    if kind == "fullname":
        return f"The name I have for you is {ident.name}. I don't have a surname on your account."
    if kind == "call":
        return f"I call you {ident.name}."
    if kind == "self":
        where = f" in {ident.city_region}" if ident.city else ""
        return f"You're {ident.name}, a member of this household{where}."
    if kind == "home":
        return f"You live in {ident.city_region}." if ident.city else None
    if kind == "address":
        if ident.street_address:
            return f"Your address is {ident.street_address}."
        if ident.city:
            return (f"I only have your location, {ident.city_region}, "
                    "not a street address.")
        return None
    return None


async def maybe_answer(text: str, user_id: str) -> Optional[tuple[str, str]]:
    """``(kind, reply)`` for an own-identity question from a registered account, else
    ``None``. Memory is never consulted for the ANSWER; it is only checked afterwards,
    in the background, to log a conflicting row (``IDENTITY_CONFLICT``). NEVER raises."""
    try:
        kind = identity_question_kind(text)
        if not kind or not _is_account_id(user_id):
            return None
        ident = await resolve_identity(user_id)
        if ident is None:
            return None
        reply = reply_for(kind, ident)
        if not reply:
            return None
        _spawn_conflict_check(user_id, kind, ident)
        return kind, reply
    except Exception as exc:  # noqa: BLE001
        logger.warning("identity_facts: tier failed (non-fatal): %r", exc)
        return None


# ── memory rows that assert the user's own identity ───────────────────────────

# "User's name is X" / "The user's full name is X" / "User is named X" / "User is called X".
_USER_SUBJ = r"(?:the\s+)?(?:user|owner|account\s+holder)(?:['’]s)?"
_NAME_ASSERT_RES: tuple[re.Pattern, ...] = (
    re.compile(r"^\W*" + _USER_SUBJ + r"\s+(?:(?:full|first|last|legal|real|given|middle)\s+)?name"
               r"\s*(?:is|was|:)\s*(?P<name>.+?)\W*$", re.IGNORECASE),
    re.compile(r"^\W*(?:the\s+)?user\s+(?:is|was)\s+(?:called|named|known\s+as)\s+(?P<name>.+?)\W*$",
               re.IGNORECASE),
)
_HOME_ASSERT_RES: tuple[re.Pattern, ...] = (
    re.compile(r"^\W*" + _USER_SUBJ + r"?\s*(?:lives|resides|is\s+living|is\s+based)\s+in\s+(?P<place>.+?)\W*$",
               re.IGNORECASE),
    re.compile(r"^\W*" + _USER_SUBJ + r"\s+(?:home|address|hometown|city)\s*(?:is|:)\s*(?P<place>.+?)\W*$",
               re.IGNORECASE),
)


def asserted_user_name(text: str) -> str:
    """The name a memory text claims is THE USER'S OWN, or "". Pure."""
    t = (text or "").strip()
    for rx in _NAME_ASSERT_RES:
        m = rx.match(t)
        if m:
            return m.group("name").strip()
    return ""


def asserted_user_home(text: str) -> str:
    t = (text or "").strip()
    for rx in _HOME_ASSERT_RES:
        m = rx.match(t)
        if m:
            return m.group("place").strip()
    return ""


def is_user_name_assertion(text: str) -> bool:
    return bool(asserted_user_name(text))


def _norm_tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t]


def name_conflicts(asserted: str, ident: Identity) -> bool:
    """True when ``asserted`` is not this account's name: neither the account name nor
    the preferred name appears as a token in it (a longer form of the real name —
    "Jason Smith" for "Jason" — is NOT a conflict)."""
    toks = set(_norm_tokens(asserted))
    if not toks:
        return False
    mine = set(_norm_tokens(ident.account_name)) | set(_norm_tokens(ident.name))
    return not (toks & mine)


def home_conflicts(asserted: str, ident: Identity) -> bool:
    if not ident.city:
        return False  # nothing to contradict
    return ident.city.lower() not in (asserted or "").lower()


async def conflicting_memory_rows(user_id: str, ident: Identity, *, svc=None,
                                  limit: int = 200) -> list[dict]:
    """Approved memory rows asserting the user's own name/home that CONFLICT with the
    account: ``[{"id", "kind", "text", "source", "added_by", "session_id"}]``.
    Read-only. NEVER raises (returns what it found)."""
    out: list[dict] = []
    try:
        if svc is None:
            from memory_service import get_memory_service  # type: ignore[import]

            svc = get_memory_service()
        rows = await svc.list_by_status(user_id=user_id, status="approved", limit=limit)
        for ref in rows or []:
            text = getattr(ref, "text", "") or ""
            md = getattr(ref, "metadata", {}) or {}
            kind = ""
            a_name = asserted_user_name(text)
            if a_name and name_conflicts(a_name, ident):
                kind = "name"
            else:
                a_home = asserted_user_home(text)
                if a_home and home_conflicts(a_home, ident):
                    kind = "home"
            if kind:
                out.append({
                    "id": getattr(ref, "id", ""), "kind": kind, "text": text,
                    "source": md.get("source", ""), "added_by": md.get("added_by", ""),
                    "reviewed_by": md.get("reviewed_by", ""), "session_id": md.get("session_id", ""),
                })
    except Exception as exc:  # noqa: BLE001
        logger.debug("identity_facts: conflict scan failed: %r", exc)
    return out


_BG: set = set()


def _spawn_conflict_check(user_id: str, kind: str, ident: Identity) -> None:
    try:
        task = asyncio.get_running_loop().create_task(_log_conflicts(user_id, kind, ident))
    except RuntimeError:
        return
    _BG.add(task)
    task.add_done_callback(_BG.discard)


async def _log_conflicts(user_id: str, kind: str, ident: Identity, *, svc=None) -> int:
    """Log ``IDENTITY_CONFLICT user=<id> kind=<name|home>`` once per conflicting memory
    row. The answer was already given from the account; this only makes the pollution
    visible. Ids and labels only — never the row's text."""
    want = "home" if kind in ("home", "address") else "name"
    try:
        rows = await asyncio.wait_for(conflicting_memory_rows(user_id, ident, svc=svc), 3.0)
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for row in rows:
        if row["kind"] == want:
            logger.warning("IDENTITY_CONFLICT user=%s kind=%s", user_id, want)
            n += 1
    return n
