"""Traffic class: is this turn the household, or our own test traffic? (P-2, deep dive 2, 2026-10-19)

Measured 2026-10-19: 97.2% of brain turns since 09-25 and 96.1% of STT calls since 10-06 were the
harness (Samantha bar, day-sim, person bench, replay probe), so every live metric mostly measured the
harness. Every turn now carries ONE of four classes, and the turn records (the ``BRAIN_LANE`` /
``FLUE_PROMPT_CACHE`` / ``VOICE TIMING`` lines, the app-log suffix, ``chat_messages.metadata``, the STT
and router shadow logs, the struggle capture) write it:

* ``household`` - a real person (a member, a guest at the real panel, Telegram). The default.
* ``harness``   - a synthetic id the harnesses mint (``demo_*``, ``bench_*``, ``zoe-hammer-<n>``, ...).
* ``probe``     - a ``probe_*`` id, a harness panel's device token, or an in-process replay.
* ``ci``        - a ``ci_*`` / ``test_*`` / ``e2e_*`` id (the self-hosted suites that post to :8000).

THE RULE: the class is derived SERVER-SIDE from the principal, never from a client claim alone.
* A synthetic id (``user_filters.SYNTHETIC_USER_RE``, the one pattern) is classified by its prefix and
  cannot be claimed household by anything.
* ``X-Zoe-Traffic-Class`` is honoured ONLY next to a valid ``X-Internal-Token`` (a holder of the service
  secret is our own tooling), and it can only name a NON-household class: a ``household`` value is
  ignored, so no header can promote a turn. A browser, the panel, or a loopback caller without the
  token is never read.
* A device token is resolved to its panel server-side (``panel_auth.lookup_device_token``); a body
  ``panel_id`` is a client claim and is not read here.
* ``ZOE_TRAFFIC_CLASS`` (harness|probe|ci) is the PROCESS default for scripts that import service
  modules and talk to the brain in-process (``tests/replay_samples.py``). Never set it in the zoe-data
  service's own .env: it would label the household's turns.

Guest sentinels are deliberately NOT synthetic here (``user_filters.is_synthetic_user`` treats them as
"not a person" for memory passes): a guest at the real panel is household traffic.

Stdlib-only; never raises from the read side (instrumentation must not break a turn).
"""
from __future__ import annotations

import contextvars
import hmac
import os
import re
import threading
from typing import Any, Mapping, Optional

HOUSEHOLD = "household"
HARNESS = "harness"
PROBE = "probe"
CI = "ci"
CLASSES: tuple[str, ...] = (HOUSEHOLD, HARNESS, PROBE, CI)
NON_HOUSEHOLD: frozenset[str] = frozenset((HARNESS, PROBE, CI))

HEADER = "X-Zoe-Traffic-Class"

# Panel ids that are our own instruments, never a room in the house. Matched as a prefix on the panel id a
# DEVICE TOKEN resolves to (server-side), never on a body claim. Mirrors struggle_detector.SYNTHETIC_PANELS.
HARNESS_PANEL_PREFIXES: tuple[str, ...] = ("replay", "test", "probe", "ci-", "e2e", "bench")

_PROBE_RE = re.compile(r"^probe[-_]", re.IGNORECASE)
_CI_RE = re.compile(r"^(?:ci|test|e2e)[-_]", re.IGNORECASE)

_current: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("zoe_traffic_class", default=None)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def process_default() -> str:
    """The class of a turn with no request context: ``ZOE_TRAFFIC_CLASS`` when it names a non-household class
    (a harness process), else household. Read per call so a test can monkeypatch it."""
    cls = _norm(os.environ.get("ZOE_TRAFFIC_CLASS"))
    return cls if cls in NON_HOUSEHOLD else HOUSEHOLD


def class_for_user(user_id: Optional[str]) -> Optional[str]:
    """The class a synthetic ``user_id`` FORCES, or None for a real id / a guest sentinel.

    The synthetic test is ``user_filters`` (one pattern + the allowlist), minus the guest sentinels: an
    allowlisted ``demo_*`` id the operator re-admitted as a person stays None here too."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    try:
        from user_filters import GUEST_USERS, is_synthetic_user
    except Exception:  # noqa: BLE001
        return None
    if uid in GUEST_USERS or not is_synthetic_user(uid):
        return None
    if _PROBE_RE.match(uid):
        return PROBE
    if _CI_RE.match(uid):
        return CI
    return HARNESS


def class_for_panel(panel_id: Optional[str]) -> Optional[str]:
    """PROBE for a harness panel id resolved from a device token, else None."""
    pid = _norm(panel_id)
    if pid and pid.startswith(HARNESS_PANEL_PREFIXES):
        return PROBE
    return None


def classify(
    user_id: Optional[str] = None,
    *,
    panel_id: Optional[str] = None,
    internal_token_valid: bool = False,
    claimed: Optional[str] = None,
    default: Optional[str] = None,
) -> str:
    """THE rule (module docstring). ``panel_id`` must be the SERVER-resolved panel (device token), and
    ``claimed`` counts only when ``internal_token_valid`` (the caller proved the service secret)."""
    forced = class_for_user(user_id)
    if forced:
        return forced
    by_panel = class_for_panel(panel_id)
    if by_panel:
        return by_panel
    if internal_token_valid:
        claim = _norm(claimed)
        if claim in NON_HOUSEHOLD:
            return claim
    base = _norm(default) if default is not None else process_default()
    return base if base in CLASSES else HOUSEHOLD


def _internal_token_valid(headers: Mapping[str, str]) -> bool:
    expected = os.environ.get("ZOE_INTERNAL_TOKEN", "")
    provided = headers.get("X-Internal-Token", "") or headers.get("x-internal-token", "")
    return bool(expected) and bool(provided) and hmac.compare_digest(str(provided), str(expected))


def _device_panel(headers: Mapping[str, str]) -> Optional[str]:
    raw = headers.get("X-Device-Token", "") or headers.get("x-device-token", "")
    if not raw:
        return None
    try:
        from routers.panel_auth import lookup_device_token

        info = lookup_device_token(str(raw))
    except Exception:  # noqa: BLE001
        return None
    return str((info or {}).get("panel_id") or "") or None


def classify_headers(headers: Mapping[str, str]) -> str:
    """Classify a request from what the SERVER can verify in its headers: the internal token (secret), the
    acting-user override it authorises, the device token's panel. Used by the request middleware, before
    the route resolves its principal; ``refine_for_user`` tightens it once the principal is known."""
    try:
        token_ok = _internal_token_valid(headers)
        acting = (headers.get("X-Zoe-User-Id", "") or headers.get("x-zoe-user-id", "")) if token_ok else ""
        return classify(
            acting or None,
            panel_id=_device_panel(headers),
            internal_token_valid=token_ok,
            claimed=headers.get(HEADER, "") or headers.get(HEADER.lower(), ""),
        )
    except Exception:  # noqa: BLE001
        return process_default()


def current() -> str:
    """The class of the turn being handled (request context), else the process default. Never raises."""
    try:
        cls = _current.get()
    except Exception:  # noqa: BLE001
        cls = None
    return cls if cls in CLASSES else process_default()


def bind(cls: str) -> contextvars.Token:
    """Set the class for this context (and every task it spawns). Returns the reset token."""
    return _current.set(cls if cls in CLASSES else HOUSEHOLD)


def reset(token: contextvars.Token) -> None:
    try:
        _current.reset(token)
    except Exception:  # noqa: BLE001
        pass


def refine_for_user(user_id: Optional[str]) -> str:
    """Once the route knows its principal: a synthetic id forces its class (only ever AWAY from household).
    Never promotes: a request already marked non-household stays so. Returns the class now in effect."""
    forced = class_for_user(user_id)
    if forced and current() == HOUSEHOLD:
        _current.set(forced)
    return current()


def detached_task(loop: Any, coro: Any, *, name: Optional[str] = None) -> Any:
    """``loop.create_task`` from an EMPTY context. A task copies the context it is created in, so a
    process-lifetime worker started lazily inside a request (and 97% of requests were the harness) would carry
    that request's class forever and tag every later household line it writes. Per-turn work keeps the plain
    ``create_task`` - it SHOULD inherit its turn's class."""
    return contextvars.Context().run(loop.create_task, coro, name=name)


def is_household(cls: Optional[str] = None) -> bool:
    return (cls if cls is not None else current()) == HOUSEHOLD


# ── per-class brain-turn tally (process lifetime; for the panel KV-miss alarm's "who used the slot") ──

_turns_lock = threading.Lock()
_brain_turns: dict[str, int] = {c: 0 for c in CLASSES}


def note_brain_turn(cls: Optional[str] = None) -> None:
    c = cls if cls in CLASSES else current()
    with _turns_lock:
        _brain_turns[c] = _brain_turns.get(c, 0) + 1


def brain_turn_counts() -> dict[str, int]:
    with _turns_lock:
        return dict(_brain_turns)


def log_suffix(cls: Optional[str] = None) -> str:
    """`` traffic=<class>`` for a non-household context, '' for household (household lines stay byte-identical,
    so every existing parser of the app log is unaffected; a harness line is greppable)."""
    c = cls if cls is not None else current()
    return f" traffic={c}" if c in NON_HOUSEHOLD else ""
