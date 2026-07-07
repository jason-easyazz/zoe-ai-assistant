"""Typed env accessors — one canonical parse per type, read at CALL time.

The bug class this ends (Wave-4 packet, Part 2): the same flag parsed
incompatibly across modules — ``ZOE_USE_CORE_BRAIN`` is ``.lower() == "true"``
in one place (so ``"1"``/``"yes"`` → False) and ``not in {"0","false","no",
"off"}`` in another (so ``"1"``/``"yes"`` → True). One parser, one truth table.

Deliberately NOT pydantic-settings, NOT a Settings singleton, NOT an
import-time snapshot, NOT a registry of keys — just parsers:

- Every accessor reads ``os.environ`` at call time (tests monkeypatch env;
  ``runtime_env`` bootstraps after import). Callers that want an import-time
  constant keep writing ``X = env_bool("FLAG", default=True)`` at module top —
  read-time semantics remain the call site's choice, so migrations stay
  mechanical.
- ``gemma_endpoint.gemma_base()`` remains the sole ``GEMMA_SERVER_URL``
  accessor (the ``/v1/v1`` 404 trap); this module does not grow URL logic.
- Unparseable values fall back to the default with ONE warning per (key,
  offending value) — a typo'd flag must be visible in the journal, not a
  silent behavior flip, and not journal spam on a per-turn read.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

# (key, raw-value) pairs already warned about — warn once per distinct problem.
_warned: set[tuple[str, str]] = set()


def _warn_once(key: str, raw: str, kind: str, default: object) -> None:
    marker = (key, raw)
    if marker in _warned:
        return
    _warned.add(marker)
    logger.warning(
        "typed_env: %s=%r is not a valid %s — using default %r", key, raw, kind, default
    )


def env_str(key: str, default: str = "") -> str:
    """String env read, stripped. Absent OR empty/whitespace-only → default
    (a `.env` line like ``KEY=`` means "not set", consistently with every
    other accessor here — callers must never receive a surprise empty
    string when they asked for a default)."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    val = raw.strip()
    return val if val else default


def env_bool(key: str, default: bool = False) -> bool:
    """Canonical bool: 1/true/yes/on ↔ 0/false/no/off (case/space-insensitive).

    Absent → default. **Present-but-empty → False**, deliberately NOT the
    default: the live ``.env`` uses ``KEY=`` as an explicit "cleared/off"
    state (3 such keys at the time of writing), and every legacy bool parse
    in the tree is truthy-set membership, where ``""`` is never truthy. If
    empty returned the default, migrating a default-true flag (e.g.
    ``ZOE_VOICE_TOOL_FILLER=`` written to disable it) would silently flip it
    ON — the exact bug class this module exists to end. Byte-equivalence with
    the legacy parses requires empty → False.

    Any other unrecognized non-empty value → default + one warning (never a
    silent flip)."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    val = raw.strip().lower()
    if not val:
        return False
    if val in _TRUTHY:
        return True
    if val in _FALSY:
        return False
    _warn_once(key, raw, "bool", default)
    return default


def env_int(key: str, default: int) -> int:
    """Integer env read. Absent/empty → default; unparseable → default + warn."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        _warn_once(key, raw, "int", default)
        return default


def env_float(key: str, default: float) -> float:
    """Float env read. Absent/empty → default; unparseable → default + warn."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        _warn_once(key, raw, "float", default)
        return default


def env_list(key: str, default: tuple[str, ...] = (), sep: str = ",") -> tuple[str, ...]:
    """Separated list, items stripped, empties dropped. Absent/empty → default."""
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return tuple(item.strip() for item in raw.split(sep) if item.strip())
