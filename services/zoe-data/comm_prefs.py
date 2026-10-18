"""comm_prefs - per-member communication preferences (who hears what Zoe changed, where and when).

The model is ``docs/architecture/communication-preferences.md`` section 3: ONE JSON document per member under the
existing ``user_prefs`` store, key ``communication``, schema-versioned and validated here. No migration.

    communication:
      version: 1
      preset: owner_technical        # owner_technical | important_only | weekly | everything | nothing | custom
      language: en                   # English only for now (owner decision 14); anything else is stored as en
      quiet_hours: {start: "22:00", end: "07:00"}   # absent = the household's ZOE_QUIET_START_HOUR / _END_HOUR
      digest: {daily_at: "19:00", weekly_on: "sun", weekly_at: "10:00"}
      daily_instant_cap: 6           # instant notices beyond this roll into the next digest
      raised: count                  # open PRs in the digest: count | list | none (owner decision 3)
      categories: {<category>: {cadence, channels, detail}}

``normalise`` is the ONE reader: an unknown or malformed field falls back to the role default field by field (a typo
never silences or widens anything), and the floors (section 3.2) are applied last, so no stored value can break them.

Phase 1 is OWNER-ONLY: ``defaults_for_role`` answers ``None`` for every non-admin role, and ``None`` means "this
member receives nothing" (guests and children never have preferences at all).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

PREF_KEY = "communication"
VERSION = 1

CATEGORIES = ("decision", "broke", "security", "fixed", "ability", "household", "under_the_hood")
CADENCES = ("instant", "digest", "weekly", "never")
CHANNELS = ("telegram", "whatsapp", "sms", "email", "push", "web_push", "panel", "first_turn", "none")
DETAILS = ("technical", "plain", "pointer")
PRESETS = ("owner_technical", "important_only", "weekly", "everything", "nothing", "custom")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RAISED = ("count", "list", "none")
LANGUAGES = ("en",)  # owner decision 14: English only until someone asks

# Roles that are the owner in P1 (auth._ADMIN_ROLES; "family-admin" is the legacy alias).
OWNER_ROLES = frozenset({"admin", "family-admin"})

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _cats(spec: dict[str, tuple[str, str]], channels: list[str]) -> dict[str, dict]:
    return {c: {"cadence": spec[c][0], "channels": list(channels), "detail": spec[c][1]} for c in CATEGORIES}


_T = "technical"
_PRESET_CATEGORIES: dict[str, dict[str, tuple[str, str]]] = {
    # Section 3.3, owner row: instant decision/broke/security, everything else in ONE evening digest.
    "owner_technical": {"decision": ("instant", _T), "broke": ("instant", _T), "security": ("instant", "plain"),
                        "fixed": ("digest", _T), "ability": ("digest", _T), "household": ("digest", _T),
                        "under_the_hood": ("digest", _T)},
    # Section 7.1 "only tell me about big stuff".
    "important_only": {"decision": ("instant", _T), "broke": ("instant", _T), "security": ("instant", "plain"),
                       "fixed": ("weekly", _T), "ability": ("weekly", _T), "household": ("weekly", _T),
                       "under_the_hood": ("weekly", _T)},
    "weekly": {c: ("weekly", _T) for c in CATEGORIES} | {"security": ("instant", "plain")},
    "everything": {c: ("instant", _T) for c in CATEGORIES} | {"security": ("instant", "plain")},
    "nothing": {c: ("never", _T) for c in CATEGORIES},
}


def preset_categories(preset: str, channels: Optional[list[str]] = None) -> dict[str, dict]:
    spec = _PRESET_CATEGORIES.get(preset) or _PRESET_CATEGORIES["owner_technical"]
    return _cats(spec, channels or ["telegram"])


def defaults_for_role(role: str) -> Optional[dict]:
    """The starting document for a role, or ``None`` = receives nothing (every non-owner in P1)."""
    if str(role or "").strip().lower() not in OWNER_ROLES:
        return None
    return {
        "version": VERSION,
        "preset": "owner_technical",
        "language": "en",
        "quiet_hours": None,  # the household's quiet hours
        "digest": {"daily_at": "19:00", "weekly_on": "sun", "weekly_at": "10:00"},
        "daily_instant_cap": 6,
        "raised": "count",
        "categories": preset_categories("owner_technical"),
    }


def _hhmm(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and _HHMM.match(v) else None


def _category(raw: Any, default: dict) -> dict:
    out = copy.deepcopy(default)
    if not isinstance(raw, dict):
        return out
    if raw.get("cadence") in CADENCES:
        out["cadence"] = raw["cadence"]
    if raw.get("detail") in DETAILS:
        out["detail"] = raw["detail"]
    ch = raw.get("channels")
    if isinstance(ch, list):
        kept = [c for c in ch if isinstance(c, str) and c in CHANNELS]
        out["channels"] = list(dict.fromkeys(kept))  # ordered, de-duplicated; [] = held nowhere = delivered nowhere
    return out


def apply_floors(prefs: dict, *, is_owner: bool) -> dict:
    """Section 3.2: ``security`` about your own account and the owner's ``decision`` can never be ``never``."""
    cats = prefs["categories"]
    if cats["security"]["cadence"] == "never":
        cats["security"]["cadence"] = "digest"
    if is_owner and cats["decision"]["cadence"] == "never":
        cats["decision"]["cadence"] = "digest"
    return prefs


def normalise(raw: Any, role: str) -> Optional[dict]:
    """The validated document for this member, or ``None`` when the role receives nothing."""
    base = defaults_for_role(role)
    if base is None:
        return None
    out = copy.deepcopy(base)
    if not isinstance(raw, dict):
        return apply_floors(out, is_owner=True)
    preset = raw.get("preset")
    if preset in PRESETS and preset != "custom":
        out["preset"] = preset
        out["categories"] = preset_categories(preset)
    elif preset == "custom":
        out["preset"] = "custom"
    if raw.get("language") in LANGUAGES:
        out["language"] = raw["language"]
    qh = raw.get("quiet_hours")
    if isinstance(qh, dict) and _hhmm(qh.get("start")) and _hhmm(qh.get("end")):
        out["quiet_hours"] = {"start": qh["start"], "end": qh["end"]}
    dg = raw.get("digest")
    if isinstance(dg, dict):
        if _hhmm(dg.get("daily_at")):
            out["digest"]["daily_at"] = dg["daily_at"]
        if dg.get("weekly_on") in WEEKDAYS:
            out["digest"]["weekly_on"] = dg["weekly_on"]
        if _hhmm(dg.get("weekly_at")):
            out["digest"]["weekly_at"] = dg["weekly_at"]
    cap = raw.get("daily_instant_cap")
    if isinstance(cap, int) and not isinstance(cap, bool) and 0 <= cap <= 50:
        out["daily_instant_cap"] = cap
    if raw.get("raised") in RAISED:
        out["raised"] = raw["raised"]
    cats = raw.get("categories")
    if isinstance(cats, dict):
        before = copy.deepcopy(apply_floors(out, is_owner=True)["categories"])
        for c in CATEGORIES:
            if c in cats:
                out["categories"][c] = _category(cats[c], out["categories"][c])
        apply_floors(out, is_owner=True)
        if out["categories"] != before:
            out["preset"] = "custom"  # any per-category edit makes the preset custom (section 3.1)
    return apply_floors(out, is_owner=True)


def household_quiet_hours() -> tuple[str, str]:
    """``ZOE_QUIET_START_HOUR`` / ``ZOE_QUIET_END_HOUR`` (the proactive engine's household window), as HH:MM."""
    from typed_env import env_int

    start = env_int("ZOE_QUIET_START_HOUR", 22) % 24
    end = env_int("ZOE_QUIET_END_HOUR", 7) % 24
    return f"{start:02d}:00", f"{end:02d}:00"


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def quiet_window(prefs: dict) -> tuple[int, int]:
    """(start, end) in minutes of the day: the member's quiet hours, or the household's when unset."""
    qh = prefs.get("quiet_hours")
    start, end = (qh["start"], qh["end"]) if isinstance(qh, dict) else household_quiet_hours()
    return _minutes(start), _minutes(end)


def in_quiet_hours(prefs: dict, minute_of_day: int) -> bool:
    """True inside the member's quiet window (or the household's when unset). Windows may wrap midnight."""
    s, e = quiet_window(prefs)
    if s == e:
        return False
    return s <= minute_of_day < e if s < e else (minute_of_day >= s or minute_of_day < e)


def merge(stored: Any, patch: Any) -> dict:
    """A partial update over the stored document: top-level keys replace, ``categories`` merge per category, and a
    named preset with no category edits replaces the categories (so ``{"preset": "weekly"}`` means weekly)."""
    out = dict(stored) if isinstance(stored, dict) else {}
    if not isinstance(patch, dict):
        return out
    for k, v in patch.items():
        if k == "categories" and isinstance(v, dict):
            cats = dict(out.get("categories") or {})
            for c, spec in v.items():
                cats[c] = {**(cats.get(c) or {}), **spec} if isinstance(spec, dict) else spec
            out["categories"] = cats
        else:
            out[k] = v
    if patch.get("preset") in PRESETS and patch.get("preset") != "custom" and "categories" not in patch:
        out.pop("categories", None)
    elif "categories" in patch and "preset" not in patch:
        out["preset"] = "custom"
    return out


async def load(user_id: str, role: str, *, db=None) -> Optional[dict]:
    """The member's validated preferences (``None`` = receives nothing)."""
    from user_prefs import get_pref

    return normalise(await get_pref(user_id, PREF_KEY, None, db=db), role)


async def save(user_id: str, role: str, raw: Any, *, db=None) -> Optional[dict]:
    """Validate and store. Returns what was stored, or ``None`` (the role cannot have preferences; nothing stored)."""
    from user_prefs import set_pref

    doc = normalise(raw, role)
    if doc is None:
        return None
    await set_pref(user_id, PREF_KEY, doc, db=db)
    return doc
