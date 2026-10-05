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

# THE WALL IS AN ALLOW-LIST. Only a source that is the user DICTATING a fact (or an
# operator tool) may assert the user's own name; EVERY other source label is automatic and
# walled - including ones nobody has thought of yet. A deny-list of automatic names cannot
# hold: ``zoe_agent._background_memory_save`` falls back to ``chat_regex_fallback`` when
# ``memory_extractor`` cannot be imported, and ``user_prefs.MEMORY_OPT_OUT_SOURCES`` omits
# the voice-lane / idle-consolidation labels - each was a hole the moment it was missed.
EXPLICIT_SOURCES = frozenset({"brain_tool", "voice_fact", "review_ui", "proposal"})
DIRECT_USER_SOURCES = EXPLICIT_SOURCES | {"identity_audit"}


def is_automatic_source(source: Optional[str], owner: Optional[str] = None) -> bool:
    """True for every writer that is not a direct user/operator source. ``owner`` (the row's
    user_id) lets ``review(edit)`` treat the owner reviewing their OWN memory (the review
    UI passes the user id as the actor) as direct."""
    src = (source or "").strip()
    return not (src in DIRECT_USER_SOURCES or (owner and src == owner))


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
    use_current_location: bool = False,
) -> Optional[Identity]:
    """Pure: the Identity the account rows imply (no I/O, no memory). ``None`` when the
    account has no name at all. ``city``/``country`` are the user's own weather prefs;
    ``sysloc`` is the household default (``system_preferences.weather_default_location``).

    ``weather_preferences.city`` is a WEATHER location. With ``use_current_location`` set
    it follows the device (a travel city is not home), so it is ignored and the household
    default stands in."""
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
    if use_current_location:
        city, country = "", ""
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


# ONE round trip (a cold lookup used to be five sequential queries — on the voice path
# that is latency spent before the brain is even called).
_IDENTITY_SQL = (
    "SELECT a.username AS username, a.settings AS settings, u.name AS users_name, "
    "p.prefs AS prefs, w.city AS wp_city, w.country AS wp_country, "
    "w.use_current_location AS wp_current, "
    "(SELECT value FROM system_preferences WHERE key = 'weather_default_location') AS sysloc "
    "FROM auth_users a "
    "LEFT JOIN users u ON u.id = a.user_id "
    "LEFT JOIN user_preferences p ON p.user_id = a.user_id "
    "LEFT JOIN weather_preferences w ON w.user_id = a.user_id "
    "WHERE a.user_id = ?"
)


def identity_from_row(uid: str, row: Any) -> Optional[Identity]:
    """Pure: ``_IDENTITY_SQL``'s row -> Identity (``None`` for no row = not an account)."""
    if row is None:
        return None
    return build_identity(
        uid,
        username=str(_col(row, "username", 0) or ""),
        settings=_json_dict(_col(row, "settings", 1)),
        users_name=str(_col(row, "users_name", 2) or ""),
        prefs=_json_dict(_col(row, "prefs", 3)),
        city=str(_col(row, "wp_city", 4) or ""),
        country=str(_col(row, "wp_country", 5) or ""),
        use_current_location=bool(_col(row, "wp_current", 6)),
        sysloc=_json_dict(_col(row, "sysloc", 7)),
    )


async def _load_identity(db, user_id: str) -> Optional[Identity]:
    uid = (user_id or "").strip()
    if not _is_account_id(uid):
        return None
    return identity_from_row(uid, await _one(db, _IDENTITY_SQL, (uid,)))


_CACHE_TTL_S = 120.0
_cache: dict[str, tuple[float, Optional[Identity]]] = {}
_LOAD_TIMEOUT_S = 1.5
_FAIL_TTL_S = 15.0


def clear_cache(user_id: Optional[str] = None) -> None:
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)


_loads: dict[str, "asyncio.Future"] = {}


async def _fetch(uid: str, db) -> Optional[Identity]:
    if db is not None:
        return await _load_identity(db, uid)
    from db_pool import get_db_ctx  # type: ignore[import]

    async with get_db_ctx() as conn:
        return await _load_identity(conn, uid)


async def resolve_identity(user_id: str, db=None, *, budget_s: Optional[float] = None) -> Optional[Identity]:
    """The account's identity, or ``None`` (guest / unregistered / DB trouble).

    Cached per user for ``_CACHE_TTL_S``. ``budget_s`` bounds how long THIS caller waits
    (default ``_LOAD_TIMEOUT_S``); a load that outlives the budget keeps running in the
    background and fills the cache for the next turn, and the caller gets the last known
    identity (even an expired one) or ``None``. A failure is remembered for
    ``_FAIL_TTL_S`` so a hung DB costs one wait per window, not one per turn.
    NEVER raises."""
    uid = (user_id or "").strip()
    if not _is_account_id(uid):
        return None
    hit = _cache.get(uid)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    wait = _LOAD_TIMEOUT_S if budget_s is None else budget_s
    try:
        task = _loads.get(uid)
        if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
            task = asyncio.ensure_future(_fetch(uid, db))
            _loads[uid] = task

            def _done(t, uid=uid):
                _loads.pop(uid, None)
                try:
                    _cache[uid] = (time.monotonic(), t.result())
                except BaseException:  # noqa: BLE001 — failure/cancel: keep the stale entry
                    old = _cache.get(uid)
                    _cache[uid] = (time.monotonic() - _CACHE_TTL_S + _FAIL_TTL_S,
                                   old[1] if old else None)
            task.add_done_callback(_done)
        return await asyncio.wait_for(asyncio.shield(task), wait)
    except Exception as exc:  # noqa: BLE001 — identity must never break a turn
        logger.debug("identity_facts: resolve failed/over budget for %s: %r", uid, type(exc).__name__)
        return hit[1] if hit is not None else None


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


# The sidecar discloses tool groups by keyword-matching the WHOLE user message, injected
# blocks included (tool-groups.ts GROUP_TRIGGERS, read on the unelided history). A city
# called "Cold Lake" or "Hot Springs" would arm the weather group on every turn, so a
# place that contains one of those words is left out of the line (the name still goes).
# Mirror of the plain-word triggers; pinned against the TS source by a test.
_GROUP_TRIGGER_WORDS = (
    r"weather|temperature|forecast|rain|raining|rainy|snow|sunny|wind|windy|umbrella|jacket|degrees|"
    r"hot|cold|washing|laundry|outside|lists?|shopping|grocer(?:y|ies)|to-?dos?|tasks?|timers?|"
    r"countdown|remind(?:er|ers)?|calendar|schedule|appointments?|meetings?|events?|agenda|notes?|"
    r"jot|journal(?:ing)?|diary|contacts?|play|pause|resume|unpause|stop|skip|next|previous|"
    r"shuffle|mute|unmute|volume|louder|quieter|spotify|music|lights?|dim|brighten"
)
_TRIGGER_RE = re.compile(r"\b(?:" + _GROUP_TRIGGER_WORDS + r")\b", re.IGNORECASE)

# An EXISTING pinned block family ("[Today" in zoe_flue_client._FLUE_CONTEXT_BLOCKS /
# the sidecar's context-blocks.ts), so older copies are elided from history without a
# sidecar change. The label is free text after "[Today ".
BLOCK_OPEN = "[Today — household context]"
BLOCK_CLOSE = "[END Today]"


def _prompt_place(ident: Identity) -> str:
    place = ident.place if ident.city else ""
    return "" if place and _TRIGGER_RE.search(place) else place


def identity_line(ident: Identity) -> str:
    place = _prompt_place(ident)
    where = f" in {place}" if place else ""
    return f"You are talking to {ident.name}, a member of this household{where}."


# A cold lookup must not stall the first token: bounded wait, the load finishes in the
# background and the next turn is warm.
_BLOCK_BUDGET_S = 0.3


async def identity_block(user_id: str) -> str:
    """The prompt block for ``user_id`` or ``""`` (flag off / not an account / lookup over
    budget with nothing cached). NEVER raises."""
    try:
        if not identity_block_enabled():
            return ""
        ident = await resolve_identity(user_id, budget_s=_BLOCK_BUDGET_S)
        if ident is None:
            return ""
        return f"{BLOCK_OPEN}\n{identity_line(ident)}\n{BLOCK_CLOSE}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("identity_facts: block skipped: %r", exc)
        return ""


# ── own-identity questions ────────────────────────────────────────────────────

_LEAD = r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe|please)[\s,]+)*"
_TAIL = r"(?:\s+(?:again|please|then|now|exactly|anyway|zoe|mate))*\W*$"


def _rx(body: str) -> re.Pattern:
    return re.compile(_LEAD + body + _TAIL, re.IGNORECASE)


_IDENTITY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("fullname", _rx(r"(?:what(?:['’]s|\s+is|\s+are)?|whats)\s+my\s+full\s*name")),
    ("surname", _rx(r"(?:what(?:['’]s|\s+is|\s+are)?|whats)\s+my\s+(?:last|sur)\s*name")),
    ("name", _rx(r"(?:what(?:['’]s|\s+is|\s+are)?|whats)\s+my\s+(?:first\s+|real\s+)?name")),
    ("name", _rx(r"do\s+you\s+(?:know|remember)\s+my\s+name")),
    ("name", _rx(r"(?:can\s+you\s+|could\s+you\s+)?(?:tell|remind)\s+me\s+(?:what\s+)?my\s+(?:first\s+)?name(?:\s+is)?")),
    ("call", _rx(r"(?:what|who)\s+(?:do|did|should)\s+you\s+call\s+me")),
    ("call", _rx(r"what\s+am\s+i\s+called")),
    ("call", _rx(r"what\s+(?:name\s+)?(?:do|did)\s+you\s+have\s+(?:for|down\s+for)\s+me")),
    ("self", _rx(r"who\s+am\s+i")),
    # HOME, present tense only. "where did I live" is the past, "what city am I in" is the
    # present LOCATION (a trip is not home), "which suburb/area" is finer than the account
    # knows — all of those fall through to the brain.
    ("home", _rx(r"where\s+do\s+(?:i|we)\s+live")),
    ("home", _rx(r"where(?:['’]s|\s+is)\s+(?:my|our)\s+(?:home|house|place)")),
    ("home_city", _rx(r"(?:which|what)\s+(?:city|town)\s+do\s+(?:i|we)\s+live\s+in")),
    ("home_region", _rx(r"(?:which|what)\s+(?:state|region)\s+do\s+(?:i|we)\s+live\s+in")),
    ("home_country", _rx(r"(?:which|what)\s+country\s+do\s+(?:i|we)\s+live\s+in")),
    ("address", _rx(r"(?:what(?:['’]s|\s+is)|whats)\s+(?:my|our)\s+(?:home\s+|street\s+|house\s+)?address")),
    ("address", _rx(r"(?:what|which)\s+address\s+do\s+you\s+have\s+for\s+me")),
)


def identity_question_kind(message: str) -> str:
    """"name" / "fullname" / "surname" / "call" / "self" / "home" / "home_city" /
    "home_region" / "home_country" / "address" for a question that asks WHO the user is
    or WHERE they live, else "". Whole-utterance anchored, so
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
    then falls through to the brain exactly as before). It never DENIES a fact the
    account merely lacks: a first-name-only account does not say "I have no surname" —
    the brain/recall may know one — it just does not answer the surname question."""
    parts = ident.account_name.split()  # the account's own name; the preferred name is a nickname
    if kind == "name":
        return f"Your name is {ident.name}."
    if kind == "fullname":
        return f"Your full name is {ident.account_name}." if len(parts) >= 2 else None
    if kind == "surname":
        return f"Your surname is {parts[-1]}." if len(parts) >= 2 else None
    if kind == "call":
        return f"I call you {ident.name}."
    if kind == "self":
        where = f" in {ident.city_region}" if ident.city else ""
        return f"You're {ident.name}, a member of this household{where}."
    if kind == "home":
        return f"You live in {ident.city_region}." if ident.city else None
    if kind == "home_city":
        return f"You live in {ident.city}." if ident.city else None
    if kind == "home_region":
        return f"You live in {ident.region}." if ident.region else None
    if kind == "home_country":
        return f"You live in {ident.country}." if ident.country else None
    if kind == "address":
        if ident.street_address:
            return f"Your address is {ident.street_address}."
        if ident.city:
            return (f"I only have your location, {ident.city_region}, "
                    "not a street address.")
        return None
    return None


# ── an explicit rename: "call me Jay" / "my name is Jay" ──────────────────────
# The user's OWN first-person rename is the one legitimate way a name changes. It is
# written to ``user_preferences.prefs["preferred_name"]`` — the SAME field the identity
# answers, the brain-prompt line and the greeting all read (the old greeting read a
# portrait field that no code ever defined) — and acknowledged. It is a settings write
# by the account holder, NOT a memory write, so it is unaffected by the writer wall
# below, which stays closed to automatic writers (digest / regex / LLM extractors).
_RENAME_STOP = frozenset("""
a an the some any my your our his her their this that those these it its i me we you he she
they them is are am was be been being not no yes and or but if when while whenever later back
soon now then today tonight tomorrow morning evening afternoon night again please maybe
sometime anytime anything something nothing everyone someone anyone one first last next time
sir madam mate boss dude crazy stupid idiot silly what who how why where which up down out
over off on in at to for of with from by about as like ever never always just only also too
very really actually basically honestly sorry thanks thank ok okay hello hi hey yeah yep nope
can could would should will shall may might must do does did
""".split())
_NAME_TOKEN = r"[A-Za-z][A-Za-z'’\-]{0,24}"
_NAME_CAP = r"(?P<name>" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,2})"
_RENAME_LEAD = r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe|please|actually|no|yes|well|just)[\s,]+)*"


def _rn(body: str) -> re.Pattern:
    return re.compile(_RENAME_LEAD + body + _TAIL, re.IGNORECASE)


_RENAME_PATTERNS: tuple[re.Pattern, ...] = (
    _rn(r"(?:(?:you\s+can|can\s+you|could\s+you|would\s+you)\s+)?(?:just\s+)?call\s+me\s+" + _NAME_CAP),
    _rn(r"my\s+name(?:['’]s|\s+is)\s+(?:actually\s+)?" + _NAME_CAP),
    _rn(r"(?:i\s+go\s+by|i\s+prefer\s+to\s+be\s+called|i\s+like\s+to\s+be\s+called|"
        r"i(?:['’]m|\s+am)\s+called|people\s+call\s+me|everyone\s+calls\s+me|friends\s+call\s+me)\s+"
        + _NAME_CAP),
)


def rename_request(message: str) -> str:
    """The name from an explicit first-person rename ("call me Jay", "my name is Jay",
    "I go by Jay"), else "". Whole-utterance anchored; a question never renames; a name
    made of stop-words ("call me later", "call me a taxi") is rejected. A bare "actually
    it's Jay" is deliberately NOT a rename (too ambiguous) — it falls to the brain. Pure."""
    text = (message or "").strip()
    if not text or len(text) > 100 or "?" in text:
        return ""
    for rx in _RENAME_PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        toks = m.group("name").split()
        while len(toks) > 1 and toks[-1].lower() in ("please", "then", "now", "again", "mate", "zoe", "thanks"):
            toks.pop()  # the greedy name swallowed the polite tail
        if any(t.lower().strip("'’-") in _RENAME_STOP for t in toks):
            return ""
        return _display(" ".join(toks))
    return ""


async def apply_rename(user_id: str, name: str) -> bool:
    """Write the preferred name to the account settings (one atomic ``jsonb ||``). NEVER raises."""
    try:
        from user_prefs import set_pref  # type: ignore[import]

        await set_pref(user_id, KEY_PREFERRED_NAME, name)
        clear_cache(user_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("identity_facts: rename write failed (non-fatal): %r", type(exc).__name__)
        return False


async def preferred_name(user_id: str) -> str:
    """The explicit preferred name ("" when none is set) — what the greeting personalises with."""
    ident = await resolve_identity(user_id)
    return ident.name if ident is not None and ident.has_preferred_name else ""


async def maybe_answer(text: str, user_id: str) -> Optional[tuple[str, str]]:
    """``(kind, reply)`` for an own-identity question from a registered account, else
    ``None`` (a rename returns ``("rename", "I'll call you X.")`` after writing the
    settings field). Memory is never consulted for the ANSWER; it is only checked afterwards,
    in the background, to log a conflicting row (``IDENTITY_CONFLICT``). NEVER raises."""
    try:
        if not _is_account_id(user_id):
            return None
        new_name = rename_request(text)
        if new_name:
            if await resolve_identity(user_id) is None:  # registered accounts only
                return None
            if not await apply_rename(user_id, new_name):
                return None
            logger.info("IDENTITY_RENAME user=%s origin=explicit", user_id)
            return "rename", f"I'll call you {new_name}."
        kind = identity_question_kind(text)
        if not kind:
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


def classify_name_assertion(asserted: str, ident: Identity, meta: Optional[dict] = None) -> str:
    """``"match"`` | ``"needs_review"`` | ``"conflict"`` for a name a memory row claims is
    the user's own.

    * match: the asserted name is the account name or the preferred name, or a shorter /
      longer form of it ("Jason" / "Jason Smith") - compared as COMPLETE names;
    * needs_review: no shared token but it LOOKS like a nickname of one (a 3+ letter
      prefix either way: "Zeddy"/"Zed") or the row came from an explicit user teach
      (``EXPLICIT_SOURCES``) — never auto-purged, never logged as pollution;
    * conflict: anything else, i.e. a name nobody in the account goes by that an
      automatic writer put there."""
    toks = _norm_tokens(asserted)
    if not toks:
        return "match"
    tset = set(toks)
    names = [set(_norm_tokens(ident.account_name)), set(_norm_tokens(ident.name))]
    # COMPLETE names, not one shared token: "Michael Smith" does not agree with the
    # account "Jason Smith" just because the surname is shared. A shorter or longer form of
    # the SAME name ("Jason" / "Jason Smith" either way round) is a match.
    if any(n and (tset <= n or n <= tset) for n in names):
        return "match"
    mine = set().union(*names)
    for t in toks:
        for m in mine:
            if t != m and min(len(t), len(m)) >= 3 and (t.startswith(m) or m.startswith(t)):
                return "needs_review"
    if (meta or {}).get("source") in EXPLICIT_SOURCES:
        return "needs_review"
    return "conflict"


def name_conflicts(asserted: str, ident: Identity, meta: Optional[dict] = None) -> bool:
    """True only for a real ``conflict`` (see ``classify_name_assertion``)."""
    return classify_name_assertion(asserted, ident, meta) == "conflict"


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
            kind, review = "", False
            a_name = asserted_user_name(text)
            verdict = classify_name_assertion(a_name, ident, md) if a_name else "match"
            if verdict != "match":
                kind, review = "name", verdict == "needs_review"
            else:
                a_home = asserted_user_home(text)
                if a_home and home_conflicts(a_home, ident):
                    kind = "home"
            if kind:
                out.append({
                    "id": getattr(ref, "id", ""), "kind": kind, "review": review, "text": text,
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
    want = "home" if kind.startswith("home") or kind == "address" else "name"
    try:
        rows = await asyncio.wait_for(conflicting_memory_rows(user_id, ident, svc=svc), 3.0)
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for row in rows:
        if row["kind"] == want and not row.get("review"):
            logger.warning("IDENTITY_CONFLICT user=%s kind=%s", user_id, want)
            n += 1
    return n
