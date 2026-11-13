"""The household authority table: what Zoe may do, for whom, without asking (Samantha initiative item 17, lane F E7).

The table is DATA (``authority_table.json`` beside this file): every action class Zoe can take x every member role ->
``allow`` / ``ask_first`` / ``never``, plus the map from each dispatchable intent to its class. This module loads it, refuses a
table that loosens a FLOOR, and answers one question with a pure function: ``decide(action_class, role, confirmed=...)``.

Floors (enforced here in code, so editing the data file cannot weaken them; a table that breaks one fails to load and every
decision is then ``never`` with reason ``table_invalid`` - fail closed):

  1. A ``high`` or ``irreversible`` class is ``never`` for a child, a guest and an unrecognised voice.
  2. An ``irreversible`` class is never ``allow`` for anyone: at most ``ask_first``.
  3. ``self_modify`` is ``never`` for every role (the owner review path is the only way to change Zoe).
  4. A guest and an unrecognised voice ``never`` read private data (voice is a password), make a personal write, drive the
     house, or write memory (guests never get memory, affect included).
  5. ``unknown`` exists and is not ``allow`` for anyone (unknown tool = ask).
  6. The classes the floors are about keep a minimum risk (``purchase`` cannot be relabelled ``low``).
  7. Every intent has a baseline class in code; the data may re-map it only to a class at least as strict for every role
     (``calendar_show`` cannot become ``read_public``); an intent with no baseline is judged against ``unknown``.

``never`` cannot be overridden: ``confirmed=True`` (the member said yes) turns ``ask_first`` into ``allow`` and nothing else.

Roles: ``owner`` (an admin account), ``adult``, ``child`` (any member not positively an adult - fail closed), ``guest`` (the
shared kiosk / guest sessions), ``unrecognised_voice`` (``voice-guest`` and the anonymous/daemon ids). ``resolve_role`` is pure;
``lookup_role`` reads the account (``auth_users.role`` + ``manner_block.is_adult``) under a time budget.

Wiring today is LOG-ONLY: ``shadow_check`` is called from ``POST /api/system/intent-dispatch`` (the brain's tool dispatch) when
``ZOE_PROBLEM_LOOP`` is ``shadow`` or ``on`` and logs ``AUTHORITY_SHADOW ... would=allow|ask|refuse``; it never changes what the
dispatch does. The log carries intent, class, role and verdict only - no user id, no slot values.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

TABLE_PATH = Path(__file__).with_name("authority_table.json")

ROLES = ("owner", "adult", "child", "guest", "unrecognised_voice")
VERDICTS = ("allow", "ask_first", "never")
RISKS = ("low", "high", "irreversible")
RESTRICTED_ROLES = ("child", "guest", "unrecognised_voice")
NO_ACCOUNT_ROLES = ("guest", "unrecognised_voice")
UNKNOWN_CLASS = "unknown"
SELF_MODIFY_CLASS = "self_modify"
PRIVATE_CLASSES = ("read_private",)
MEMORY_CLASSES = ("memory_write_stated", "memory_write_inferred", "memory_forget")
# Floor 4: no account = no private read, no personal write, no driving the house, no memory.
NO_ACCOUNT_NEVER_CLASSES = PRIVATE_CLASSES + ("personal_write", "home_control") + MEMORY_CLASSES
_STRICTNESS = {"allow": 0, "ask_first": 1, "never": 2}
# Ids that are a voice nobody recognised (the voice lane's fallback) or no person at all.
UNRECOGNISED_IDS = frozenset({"", "voice-guest", "anonymous", "voice-daemon"})
# Slots that make a calendar write involve other people (calendar_shared, not personal_write).
_SHARED_CALENDAR_SLOTS = ("attendees", "invitees", "participants", "guests")
LOOKUP_BUDGET_S = 0.5
_RISK_ORDER = {r: i for i, r in enumerate(RISKS)}
# Floor 6: the minimum risk of the classes the floors are about - so a data edit cannot dodge Floors 1-2 by relabelling
# ``purchase`` as ``low``.
_MIN_RISK = {
    "messaging": "irreversible", "purchase": "irreversible", "memory_forget": "irreversible",
    "irreversible_other": "irreversible", "self_modify": "irreversible",
    "memory_write_inferred": "high", "calendar_shared": "high", "home_security": "high", "settings": "high", "unknown": "high",
}
# Floor 7: the BASELINE class of every known intent, held in code. The data file may re-map an intent only to a class that is,
# role by role, at least as strict as its baseline - so ``calendar_show`` cannot be moved to ``read_public`` (voice is a
# password) nor ``memory_retire`` to a read. An intent with no baseline must map to a class at least as strict as ``unknown``.
_INTENT_BASELINE = {
    "weather": "read_public", "time_query": "read_public", "date_query": "read_public", "calculate": "read_public",
    "calendar_show": "read_private", "reminder_list": "read_private", "note_search": "read_private",
    "people_search": "read_private", "journal_streak": "read_private", "journal_prompt": "read_private",
    "daily_briefing": "read_private", "alarm_list": "read_private",
    "list_show": "household_list", "list_add": "household_list", "list_remove": "household_list",
    "timer_create": "panel_utility", "music_play": "panel_utility", "music_control": "panel_utility",
    "music_volume": "panel_utility", "set_volume": "panel_utility",
    "reminder_create": "personal_write", "calendar_create": "personal_write", "note_create": "personal_write",
    "journal_create": "personal_write", "alarm_set": "personal_write",
    "smart_home": "home_control",
    "memory_store": "memory_write_stated", "people_create": "memory_write_stated",
    "memory_retire": "memory_forget",
    "music_setup": "settings",
}


class AuthorityTableError(ValueError):
    """The table is malformed or loosens a floor."""


@dataclass(frozen=True)
class IntentSpec:
    action_class: str
    receipt: str = ""          # the write_receipts kind that proves this intent landed ("" = no receipt exists)


@dataclass(frozen=True)
class Table:
    classes: Mapping[str, Mapping[str, Any]]   # class -> {"risk", "what", "rows": {role: verdict}}
    intents: Mapping[str, IntentSpec]


@dataclass(frozen=True)
class Decision:
    action_class: str
    role: str
    verdict: str        # the table cell: allow | ask_first | never
    outcome: str        # what would happen now: allow | ask | refuse
    reason: str = ""


def validate(raw: Mapping[str, Any]) -> Table:
    """Parse and check a table; raises ``AuthorityTableError`` on any malformed row or broken floor."""
    if not isinstance(raw, Mapping):
        raise AuthorityTableError("table is not an object")
    classes = raw.get("classes")
    intents = raw.get("intents")
    if not isinstance(classes, Mapping) or not classes:
        raise AuthorityTableError("no classes")
    if not isinstance(intents, Mapping):
        raise AuthorityTableError("no intents")
    if tuple(raw.get("roles") or ()) != ROLES:
        raise AuthorityTableError("roles must be exactly " + ",".join(ROLES))
    for name, spec in classes.items():
        if not isinstance(spec, Mapping):
            raise AuthorityTableError(f"{name}: not an object")
        risk = spec.get("risk")
        if risk not in RISKS:
            raise AuthorityTableError(f"{name}: risk {risk!r}")
        rows = spec.get("rows")
        if not isinstance(rows, Mapping) or set(rows) != set(ROLES):
            raise AuthorityTableError(f"{name}: rows must name every role exactly once")
        for role, verdict in rows.items():
            if verdict not in VERDICTS:
                raise AuthorityTableError(f"{name}/{role}: verdict {verdict!r}")
        # Floor 1
        if risk in ("high", "irreversible"):
            for role in RESTRICTED_ROLES:
                if rows[role] != "never":
                    raise AuthorityTableError(f"floor: {name} is {risk}, {role} must be never")
        # Floor 2
        if risk == "irreversible" and any(v == "allow" for v in rows.values()):
            raise AuthorityTableError(f"floor: {name} is irreversible, nobody may be allow")
        # Floor 4
        if name in NO_ACCOUNT_NEVER_CLASSES:
            for role in NO_ACCOUNT_ROLES:
                if rows[role] != "never":
                    raise AuthorityTableError(f"floor: {name} must be never for {role}")
    # Floor 3
    sm = classes.get(SELF_MODIFY_CLASS)
    if not sm or any(v != "never" for v in sm["rows"].values()):
        raise AuthorityTableError("floor: self_modify must exist and be never for every role")
    # Floor 5
    unk = classes.get(UNKNOWN_CLASS)
    if not unk or any(v == "allow" for v in unk["rows"].values()):
        raise AuthorityTableError("floor: unknown must exist and allow nobody")
    for floor_class in NO_ACCOUNT_NEVER_CLASSES:
        if floor_class not in classes:
            raise AuthorityTableError(f"floor: {floor_class} must exist")
    # Floor 6
    for name, min_risk in _MIN_RISK.items():
        if name not in classes or _RISK_ORDER[classes[name]["risk"]] < _RISK_ORDER[min_risk]:
            raise AuthorityTableError(f"floor: {name} must exist with risk >= {min_risk}")
    parsed: dict[str, IntentSpec] = {}
    for intent, spec in intents.items():
        if not isinstance(spec, Mapping) or spec.get("class") not in classes:
            raise AuthorityTableError(f"intent {intent}: unknown class")
        # Floor 7
        baseline = _INTENT_BASELINE.get(intent, UNKNOWN_CLASS)
        if baseline not in classes:
            raise AuthorityTableError(f"floor: baseline class {baseline} of {intent} must exist")
        mapped_rows, base_rows = classes[spec["class"]]["rows"], classes[baseline]["rows"]
        for role in ROLES:
            if _STRICTNESS[mapped_rows[role]] < _STRICTNESS[base_rows[role]]:
                raise AuthorityTableError(f"floor: intent {intent} re-mapped to a looser class for {role} (baseline {baseline})")
        parsed[intent] = IntentSpec(str(spec["class"]), str(spec.get("receipt") or ""))
    return Table(classes={k: dict(v) for k, v in classes.items()}, intents=parsed)


def load(path: Optional[Path] = None) -> Table:
    """Read and validate the table file (raises ``AuthorityTableError`` / ``OSError``)."""
    p = path or TABLE_PATH
    try:
        raw = json.loads(Path(p).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthorityTableError(f"not JSON: {exc}") from exc
    return validate(raw)


_CACHED: Optional[Table] = None
_CACHE_ERROR: str = ""


def table() -> Optional[Table]:
    """The shipped table, loaded once; None when it failed to load (every decision is then ``never``)."""
    global _CACHED, _CACHE_ERROR
    if _CACHED is None and not _CACHE_ERROR:
        try:
            _CACHED = load()
        except Exception as exc:  # noqa: BLE001 - fail closed, say why once
            _CACHE_ERROR = f"{type(exc).__name__}: {exc}"
            logger.error("AUTHORITY_TABLE invalid, every decision is never: %s", _CACHE_ERROR)
    return _CACHED


def reset_cache() -> None:
    """Test hook."""
    global _CACHED, _CACHE_ERROR
    _CACHED, _CACHE_ERROR = None, ""


def classify_intent(intent: str, slots: Optional[Mapping[str, Any]] = None, *, tbl: Optional[Table] = None) -> str:
    """The action class of one dispatchable intent (``unknown`` for anything the table does not name)."""
    t = tbl if tbl is not None else table()
    if t is None:
        return UNKNOWN_CLASS
    spec = t.intents.get((intent or "").strip())
    if spec is None:
        return UNKNOWN_CLASS
    if spec.action_class == "personal_write" and intent == "calendar_create" and slots:
        if any(slots.get(k) for k in _SHARED_CALENDAR_SLOTS):
            return "calendar_shared"
    return spec.action_class


def receipt_kind(intent: str, *, tbl: Optional[Table] = None) -> str:
    """The ``write_receipts`` kind that proves ``intent`` landed, or "" when no receipt exists for it."""
    t = tbl if tbl is not None else table()
    spec = t.intents.get(intent) if t is not None else None
    return spec.receipt if spec else ""


def class_risk(action_class: str, *, tbl: Optional[Table] = None) -> str:
    t = tbl if tbl is not None else table()
    spec = t.classes.get(action_class) if t is not None else None
    return str(spec["risk"]) if spec else "high"


def decide(action_class: str, role: str, *, confirmed: bool = False, tbl: Optional[Table] = None) -> Decision:
    """Pure: may ``role`` do ``action_class`` now? ``confirmed`` = the member already said yes to THIS action.

    ``never`` is final whatever ``confirmed`` says; ``ask_first`` becomes ``allow`` only when confirmed; an unknown class is
    judged as ``unknown``; an unknown role as ``unrecognised_voice``; an invalid table refuses everything."""
    t = tbl if tbl is not None else table()
    r = role if role in ROLES else "unrecognised_voice"
    if t is None:
        return Decision(action_class, r, "never", "refuse", "table_invalid")
    cls = action_class if action_class in t.classes else UNKNOWN_CLASS
    verdict = str(t.classes[cls]["rows"][r])
    if verdict == "allow":
        return Decision(cls, r, verdict, "allow", "table")
    if verdict == "ask_first":
        return Decision(cls, r, verdict, "allow" if confirmed else "ask", "confirmed" if confirmed else "table")
    return Decision(cls, r, "never", "refuse", "never")


def resolve_role(user_id: Optional[str], *, auth_role: str = "", adult: bool = False) -> str:
    """Pure: the table role for an acting id. ``auth_role`` is the account's role string, ``adult`` whether the member is
    positively an adult (``manner_block.is_adult``). Fail closed: a member not known to be an adult is a child."""
    uid = (user_id or "").strip()
    if uid.lower() in UNRECOGNISED_IDS:
        return "unrecognised_voice"
    if uid in ("guest",) or uid.startswith("guest-") or (auth_role or "").strip().lower() == "guest":
        return "guest"
    if not adult:
        return "child"
    if (auth_role or "").strip().lower() in ("admin", "family-admin"):
        return "owner"
    return "adult"


async def _account_role(user_id: str) -> str:
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        cur = await db.execute("SELECT role FROM auth_users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
    return str((row[0] if row else "") or "")


async def lookup_role(user_id: Optional[str]) -> str:
    """The table role for an acting id, reading the account under a budget. Never raises; a lookup failure for a member id is
    ``child`` (the most restricted member role)."""
    uid = (user_id or "").strip()
    pre = resolve_role(uid, adult=True)
    if pre in NO_ACCOUNT_ROLES:
        return pre
    try:
        from manner_block import is_adult

        auth_role = await asyncio.wait_for(_account_role(uid), timeout=LOOKUP_BUDGET_S)
        if not auth_role:
            return "unrecognised_voice"          # an id with no account is nobody we know
        adult = await is_adult(uid)
        return resolve_role(uid, auth_role=auth_role, adult=adult)
    except Exception as exc:  # noqa: BLE001
        logger.warning("authority: role lookup failed (%s); judged as child", type(exc).__name__)
        return "child"


def mode() -> str:
    """``ZOE_PROBLEM_LOOP``: ``off`` (default) | ``shadow`` (log-only authority at dispatch) | ``on`` (the same as shadow: the multi-step problem loop it once also enabled was never wired and was deleted, gap audit 2026-11-13)."""
    try:
        from typed_env import flag

        return str(flag("ZOE_PROBLEM_LOOP"))
    except Exception:  # noqa: BLE001 - registry unavailable: off (fail dark)
        return "off"


def shadow_enabled() -> bool:
    return mode() in ("shadow", "on")


_WOULD = {"allow": "allow", "ask": "ask", "refuse": "refuse"}


async def shadow_check(intent: str, slots: Optional[Mapping[str, Any]], user_id: Optional[str]) -> Optional[Decision]:
    """Log what the table WOULD say about one dispatch; change nothing. Off unless ``ZOE_PROBLEM_LOOP`` is shadow/on.
    Never raises. Returns the decision (for tests) or None when off / on failure."""
    try:
        if not shadow_enabled():
            return None
        role = await lookup_role(user_id)
        cls = classify_intent(intent, slots)
        d = decide(cls, role)
        logger.info("AUTHORITY_SHADOW intent=%s class=%s role=%s verdict=%s would=%s",
                    intent, d.action_class, d.role, d.verdict, _WOULD[d.outcome])
        return d
    except Exception as exc:  # noqa: BLE001 - evidence gathering must never touch the dispatch
        logger.warning("AUTHORITY_SHADOW failed (%s)", type(exc).__name__)
        return None
