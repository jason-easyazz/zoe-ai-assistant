"""Household locale: ONE place that says which country's conventions the brain writes in (``ZOE_LOCALE_HINT``, default on).

THE MISS (QA 2026-11-06, demo household): a recipe for an Australian household came back "bake it at 375 degrees Fahrenheit" with
US spelling ("flavored"), and a follow-up "what's that in celsius" was needed. Nothing told the brain where the house is in terms of
units and spelling: the identity line names a city, and a 4B model writes US conventions by default.

WHAT IT DOES. ``prompt_line()`` renders one sentence from ``locale_data/household_locale.json`` for the household country, e.g.
"This household is in Australia: give measurements in metric (...), oven settings in Celsius, write in Australian English (...) and
use local words (...)." The Flue seam (``zoe_flue_client``) sends it on every turn as a ``zoe-locale:`` envelope line that the
sidecar moves into that turn's SYSTEM prompt, labelled as background (``src/household-locale.ts``). It is never in the user's
message: there the brain read it as something the user said ("I'll make sure to use metric from now on", QA round 3b). Every
member gets it, children and guests included: it is a fact about the house, not about a person.

WHERE THE COUNTRY COMES FROM (first hit wins; nothing is guessed):
  1. ``ZOE_HOUSEHOLD_COUNTRY`` (ISO code or a country name), else ``ZOE_LOCATION_COUNTRY`` - ``explicit_country()``, the SAME
     answer the distress hand-off uses for its crisis lines (``distress_handoff.config``);
  2. the country of ``ZOE_TIMEZONE`` when that is EXPLICITLY set (the data file's ``zones``: a full zone, or a prefix ending
     in "/"). The code default zone is never used here: an unconfigured box gets no line rather than another country's;
  3. nothing: no line, the turn's bytes are exactly what they were.
A country with no entry in the data file also gets no line. ``ZOE_LOCALE_FILE`` (same shape) overrides entries.

Stdlib only. Never raises into a turn.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from typed_env import env_bool, env_str

logger = logging.getLogger(__name__)

FLAG = "ZOE_LOCALE_HINT"
DATA_FILE = Path(__file__).with_name("locale_data") / "household_locale.json"
#: the rendered line never exceeds this (chars); a longer one is dropped, never truncated
MAX_LINE_CHARS = 320

_ALIASES = {"UK": "GB"}


def enabled() -> bool:
    """``ZOE_LOCALE_HINT`` - default ON (a kill switch); read per call."""
    return env_bool("ZOE_LOCALE_HINT", True)  # literal on purpose: tools/audit/flag_inventory.py greps it


def explicit_country() -> str:
    """The household country the operator SET, as an upper-case ISO code ("AU"), or "" when none is set. A country name
    ("Australia") is mapped to its code; "UK" is "GB". The distress hand-off and the locale line read this one answer."""
    raw = (env_str("ZOE_HOUSEHOLD_COUNTRY", "") or env_str("ZOE_LOCATION_COUNTRY", "")).strip()
    if len(raw) > 2:
        try:
            from identity_facts import _COUNTRY_NAMES

            raw = next((k for k, v in _COUNTRY_NAMES.items() if v.lower() == raw.lower()), raw)
        except Exception:  # noqa: BLE001
            pass
    up = raw.upper()
    return _ALIASES.get(up, up)


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        logger.warning("household_locale: data file unreadable (%s)", Path(path).name)
        return {}


def _tables() -> tuple[dict, dict]:
    """(override, shipped) data tables."""
    return _load(env_str("ZOE_LOCALE_FILE", "")), _load(str(DATA_FILE))


def _zone_country(zone: str) -> str:
    for table in _tables():
        zones = table.get("zones") or {}
        if zone in zones:
            return str(zones[zone]).upper()
        for key, code in zones.items():
            if key.endswith("/") and zone.startswith(key):
                return str(code).upper()
    return ""


def country() -> str:
    """The household country for conventions: the explicit setting, else the country of an explicitly set ``ZOE_TIMEZONE``,
    else "". Pure apart from the env read."""
    code = explicit_country()
    if code:
        return code
    zone = env_str("ZOE_TIMEZONE", "").strip()  # EXPLICIT only: the code default zone is never a country hint
    return _zone_country(zone) if zone else ""


def conventions(code: str) -> dict:
    """The data entry for ``code`` (the override file first), or {}."""
    for table in _tables():
        entry = (table.get("countries") or {}).get((code or "").upper())
        if isinstance(entry, dict):
            return entry
    return {}


def _template() -> str:
    for table in _tables():
        if isinstance(table.get("line"), str) and table["line"].strip():
            return table["line"].strip()
    return ""


def render(code: str) -> str:
    """The line for ``code``, or "" (no entry, a missing field, or over ``MAX_LINE_CHARS``). Pure."""
    entry = conventions(code)
    template = _template()
    if not entry or not template:
        return ""
    try:
        line = template.format(**{k: str(v) for k, v in entry.items()})
    except (KeyError, IndexError, ValueError):
        logger.warning("household_locale: entry %s does not fill the line template; no line", code)
        return ""
    line = " ".join(line.split())
    if len(line) > MAX_LINE_CHARS or "[" in line or "]" in line:
        logger.warning("household_locale: line for %s rejected (length or brackets); no line", code)
        return ""
    return line


def prompt_line() -> str:
    """The household's conventions line for the brain, or "" (flag off / no country / no entry). Never raises."""
    try:
        if not enabled():
            return ""
        code = country()
        return render(code) if code else ""
    except Exception:  # noqa: BLE001 - a hint is optional; the turn is not
        logger.debug("household_locale: prompt line skipped", exc_info=True)
        return ""
