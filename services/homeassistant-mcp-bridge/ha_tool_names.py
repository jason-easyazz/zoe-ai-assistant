"""Home Assistant LLM tool-name scheme — legacy (< 2026.9) vs domain-prefixed.

HA 2026.9 (core PR #179938, merged 2026-08-24) prefixes every LLM / Assist-API /
MCP-server tool name with the domain of the integration that OFFERS it, using a
double-underscore separator: ``GetLiveContext`` → ``homeassistant__GetLiveContext``,
``HassTurnOn`` → ``intent__HassTurnOn``, ``GetDateTime`` → ``llm__GetDateTime``,
scripts → ``script__<script_name>``. The subtlety the release notes flatten: an
intent is prefixed by the integration that REGISTERS it, not by ``intent`` across
the board — ``light__HassLightSet``, ``climate__HassClimateSetTemperature``,
``assist_satellite__HassBroadcast``, ``todo__HassListAddItem``. Two tools were
renamed outright (``calendar_get_events`` → ``calendar__get_events``,
``todo_get_items`` → ``todo__get_items``). Unprefixed names are reported via
``frame.report_usage`` from 2026.9 and STOP WORKING in 2027.3
(``TOOL_PREFIX_BREAKS_IN_HA_VERSION``).

This module is the single place Zoe spells an HA tool name. Callers pass the
BASE name (``"HassTurnOn"``) and get the name the connected HA expects; inbound
names in either scheme normalise back to the base. Unknown names are rejected,
never passed through. The scheme is detected once from ``/api/config``
``version`` and cached; ``HA_TOOL_NAME_SCHEME=legacy|prefixed`` pins it for an
upgrade window where detection is not wanted.

Source of truth for the table: home-assistant/core tag 2026.9.3,
``homeassistant/components/<domain>/llm.py`` + each domain's intent constants.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Mapping, Optional, Tuple

logger = logging.getLogger("ha_tool_names")

SEPARATOR = "__"
LEGACY = "legacy"
PREFIXED = "prefixed"
SCHEMES = (LEGACY, PREFIXED)

# First HA release that ships prefixed names, and the one that refuses unprefixed.
PREFIXED_SINCE: Tuple[int, int] = (2026, 9)
UNPREFIXED_BREAKS_IN: Tuple[int, int] = (2027, 3)

# base name -> owning domain (the namespace HA >= 2026.9 prefixes it with).
# Grouped by the integration whose llm.py offers the tool.
TOOL_DOMAINS: Dict[str, str] = {
    # homeassistant/components/homeassistant/llm.py
    "GetLiveContext": "homeassistant",
    # homeassistant/components/llm/llm.py
    "GetDateTime": "llm",
    # homeassistant/components/intent/llm.py (LLM_INTENTS + TIMER_INTENTS)
    "HassTurnOn": "intent",
    "HassTurnOff": "intent",
    "HassCancelAllTimers": "intent",
    "HassSetPosition": "intent",
    "HassStopMoving": "intent",
    "HassStartTimer": "intent",
    "HassCancelTimer": "intent",
    "HassIncreaseTimer": "intent",
    "HassDecreaseTimer": "intent",
    "HassPauseTimer": "intent",
    "HassUnpauseTimer": "intent",
    "HassTimerStatus": "intent",
    # per-domain llm.py platforms
    "HassLightSet": "light",
    "HassClimateSetTemperature": "climate",
    "HassFanSetSpeed": "fan",
    "HassHumidifierMode": "humidifier",
    "HassHumidifierSetpoint": "humidifier",
    "HassLawnMowerDock": "lawn_mower",
    "HassLawnMowerStartMowing": "lawn_mower",
    "HassMediaNext": "media_player",
    "HassMediaPause": "media_player",
    "HassMediaPrevious": "media_player",
    "HassMediaSearchAndPlay": "media_player",
    "HassMediaUnpause": "media_player",
    "HassMediaPlayerMute": "media_player",
    "HassMediaPlayerUnmute": "media_player",
    "HassSetVolume": "media_player",
    "HassSetVolumeRelative": "media_player",
    "HassVacuumCleanArea": "vacuum",
    "HassVacuumReturnToBase": "vacuum",
    "HassVacuumStart": "vacuum",
    "HassBroadcast": "assist_satellite",
    "HassListAddItem": "todo",
    "HassListCompleteItem": "todo",
    "HassListRemoveItem": "todo",
}

# Tools whose legacy spelling differs from "<base>" (renamed, not just prefixed).
# base name -> (legacy name, prefixed name)
RENAMED_TOOLS: Dict[str, Tuple[str, str]] = {
    "calendar_get_events": ("calendar_get_events", "calendar__get_events"),
    "todo_get_items": ("todo_get_items", "todo__get_items"),
}

SCRIPT_DOMAIN = "script"
# HA object-id rule (``homeassistant.core.valid_entity_id``): lowercase ``[a-z0-9_]``,
# no leading or trailing ``_``, never ``__`` inside. The ``__`` exclusion is what
# keeps ``script__<id>`` unambiguous; the no-leading-``_`` rule is what makes the
# legacy digit prefix (``_3am_check``) reversible.
_SCRIPT_ID_RE = re.compile(r"^(?!_)(?!.*__)[a-z0-9_]+(?<!_)$")


class UnknownHaToolError(ValueError):
    """Raised for a tool name Zoe does not know in either scheme."""


def parse_ha_version(version: str) -> Tuple[int, int, int]:
    """``"2026.9.3"`` → ``(2026, 9, 3)``; ``"2026.10.0b3"`` → ``(2026, 10, 0)``.

    HA's ``/api/config`` ``version`` is CalVer ``YYYY.M.patch`` with optional
    ``bN``/``devN`` suffixes. Anything that does not start ``YYYY.M`` is rejected.
    """
    m = re.match(r"^\s*(\d{4})\.(\d{1,2})(?:\.(\d+))?", version or "")
    if not m:
        raise ValueError(f"unrecognised Home Assistant version: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def scheme_for_version(version: str) -> str:
    """Which tool-name scheme the given HA core version speaks."""
    year, month, _ = parse_ha_version(version)
    return PREFIXED if (year, month) >= PREFIXED_SINCE else LEGACY


def _names_for(base: str) -> Tuple[str, str]:
    """(legacy, prefixed) for a known base name, else UnknownHaToolError."""
    if base in RENAMED_TOOLS:
        return RENAMED_TOOLS[base]
    domain = TOOL_DOMAINS.get(base)
    if domain is None:
        raise UnknownHaToolError(f"unknown Home Assistant LLM tool: {base!r}")
    return base, f"{domain}{SEPARATOR}{base}"


def ha_tool_name(base: str, scheme: str) -> str:
    """Spell a known base tool name the way ``scheme`` expects it."""
    if scheme not in SCHEMES:
        raise ValueError(f"unknown tool-name scheme: {scheme!r}")
    legacy, prefixed = _names_for(base)
    return prefixed if scheme == PREFIXED else legacy


def script_tool_name(script_id: str, scheme: str) -> str:
    """Tool name for an HA script exposed to the LLM API.

    ``script_id`` is the object id (``script.morning`` → ``"morning"``). Legacy
    HA used the bare name, prefixing ``_`` when it started with a digit
    (that rule is dropped in 2026.9 because ``script__`` already leads).
    """
    if scheme not in SCHEMES:
        raise ValueError(f"unknown tool-name scheme: {scheme!r}")
    sid = script_id.split(".", 1)[1] if script_id.startswith("script.") else script_id
    if not _SCRIPT_ID_RE.match(sid):
        raise UnknownHaToolError(f"not a Home Assistant script object id: {script_id!r}")
    if scheme == PREFIXED:
        return f"{SCRIPT_DOMAIN}{SEPARATOR}{sid}"
    return f"_{sid}" if sid[0].isdigit() else sid


def _legacy_script_id(name: str) -> Optional[str]:
    """Undo ``script_tool_name(..., LEGACY)``: ``morning`` → ``morning``,
    ``_3am_check`` → ``3am_check``; ``None`` when ``name`` is not a spelling a
    legacy HA would emit for any valid script object id."""
    sid = name[1:] if name.startswith("_") and name[1:2].isdigit() else name
    if not _SCRIPT_ID_RE.match(sid):
        return None
    # a digit-leading id is always emitted WITH the ``_``; the bare form never is
    if sid[0].isdigit() and not name.startswith("_"):
        return None
    return sid


def normalize_tool_name(name: str, scheme: Optional[str] = None) -> str:
    """Accept a tool name in EITHER scheme and return Zoe's base name.

    Rejects unknown names (no pass-through) so a typo, a tool from a
    not-yet-supported HA integration, or a bogus ``foo__bar`` cannot leak
    through as if it were a valid tool. Script tools normalise to
    ``script.<id>`` — an entity id, so callers can tell them from core tools.

    ``scheme`` is the scheme the CONNECTED HA speaks. It is needed for one case
    only: a legacy HA names scripts by their bare object id (``morning``,
    ``_3am_check``), which is indistinguishable from an unknown bare name
    unless the caller knows the HA is legacy. So a bare, script-shaped name is
    accepted as ``script.<id>`` ONLY when ``scheme == LEGACY`` — and only after
    the base table has been checked, since ``calendar_get_events`` is also
    script-shaped. With no scheme (or the prefixed one) such names are rejected.
    """
    if scheme is not None and scheme not in SCHEMES:
        raise ValueError(f"unknown tool-name scheme: {scheme!r}")
    if not isinstance(name, str) or not name:
        raise UnknownHaToolError(f"unknown Home Assistant LLM tool: {name!r}")
    if name in TOOL_DOMAINS or name in RENAMED_TOOLS:
        return name
    for base, (legacy, prefixed) in RENAMED_TOOLS.items():
        if name in (legacy, prefixed):
            return base
    if SEPARATOR in name:
        domain, _, base = name.partition(SEPARATOR)
        if domain == SCRIPT_DOMAIN and _SCRIPT_ID_RE.match(base):
            return f"script.{base}"
        if TOOL_DOMAINS.get(base) == domain:
            return base
    elif scheme == LEGACY:
        sid = _legacy_script_id(name)
        if sid is not None:
            return f"script.{sid}"
    raise UnknownHaToolError(f"unknown Home Assistant LLM tool: {name!r}")


def tool_table(scheme: str) -> Dict[str, str]:
    """Every known base name → its spelling under ``scheme`` (for prompts/docs)."""
    names = list(TOOL_DOMAINS) + list(RENAMED_TOOLS)
    return {base: ha_tool_name(base, scheme) for base in names}


@dataclass
class SchemeDetection:
    scheme: str
    ha_version: Optional[str]
    source: str  # "env" | "api" | "default"


class HaToolNameSchemeDetector:
    """Detect the scheme once from HA ``/api/config`` and cache it.

    ``fetch_config`` is an async callable returning the ``/api/config`` JSON.
    Detection order: ``HA_TOOL_NAME_SCHEME`` env pin → ``/api/config`` version
    → ``default`` (only when ``fail_open`` is set; otherwise the error
    propagates so a caller never silently spells names for the wrong HA).
    The chosen scheme is logged exactly once per process/cache lifetime.
    Concurrent cold calls are serialised on an ``asyncio.Lock`` so ``/api/config``
    is read once, not once per racing request; the lock is created lazily so the
    detector can be built at import time before any event loop exists.
    """

    def __init__(
        self,
        fetch_config: Callable[[], Awaitable[Mapping]],
        *,
        env: Optional[Mapping[str, str]] = None,
        default: str = LEGACY,
    ) -> None:
        self._fetch_config = fetch_config
        self._env = os.environ if env is None else env
        self._default = default
        self._cached: Optional[SchemeDetection] = None
        self._lock: Optional[asyncio.Lock] = None

    @property
    def cached(self) -> Optional[SchemeDetection]:
        return self._cached

    def reset(self) -> None:
        self._cached = None

    async def detect(self, *, fail_open: bool = False) -> SchemeDetection:
        if self._cached is not None:
            return self._cached
        if self._lock is None:  # no await between check and set: safe within one loop
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._cached is not None:  # a racing call finished detection while we waited
                return self._cached
            return await self._detect_uncached(fail_open=fail_open)

    async def _detect_uncached(self, *, fail_open: bool) -> SchemeDetection:
        pinned = (self._env.get("HA_TOOL_NAME_SCHEME") or "").strip().lower()
        if pinned:
            if pinned not in SCHEMES:
                raise ValueError(f"HA_TOOL_NAME_SCHEME must be one of {SCHEMES}, got {pinned!r}")
            result = SchemeDetection(scheme=pinned, ha_version=None, source="env")
        else:
            try:
                config = await self._fetch_config()
                version = str(config.get("version", ""))
                result = SchemeDetection(scheme_for_version(version), version, "api")
            except Exception as exc:  # noqa: BLE001 — surface, don't guess, unless told to
                if not fail_open:
                    raise
                logger.warning("HA tool-name scheme detection failed (%s); defaulting to %s", exc, self._default)
                result = SchemeDetection(scheme=self._default, ha_version=None, source="default")
        self._cached = result
        logger.info(
            "HA LLM tool-name scheme: %s (ha_version=%s, source=%s; prefixed since %s, unprefixed breaks in %s)",
            result.scheme,
            result.ha_version,
            result.source,
            ".".join(map(str, PREFIXED_SINCE)),
            ".".join(map(str, UNPREFIXED_BREAKS_IN)),
        )
        return result
