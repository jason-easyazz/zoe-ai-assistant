"""Which TRAFFIC a turn is: a household request, or the test harness / a probe.

Measured 2026-10-19 (deep dive 2, lane L): 97% of brain turns and 96% of STT calls on the live box were the harness. A metric, a
label or a training example built without this tag mostly measures the harness. Every per-turn record the struggle detector
writes carries the class, and nothing that is not ``household`` can ever become a training example.

Pure and stdlib-only (``user_filters`` is stdlib too). The rules, any one of which makes a turn ``harness``:

* the process declares itself a harness (``ZOE_HARNESS=1``, the ``live_store_guard`` convention);
* an explicit hint says so (``X-Zoe-Traffic: harness|probe|test`` once a caller plumbs it through - deep dive P-2);
* the panel id is a harness / test panel (``replay-*``, ``test*``, ``probe*``, ``ci-*``, ``e2e*``, ``bench*``);
* the session id is a harness session (``replay-<hex>`` from ``tests/replay_samples.py``, ``test*``, ``probe*``, ...);
* the user id is a synthetic one (``user_filters.is_synthetic_user``: ``demo_*``, ``test-*``, ``zoe-hammer-*`` ...). A GUEST
  sentinel is not synthetic here: a guest at the real panel is household traffic, just unidentified.
"""
from __future__ import annotations

import os
from typing import Optional

HOUSEHOLD = "household"
HARNESS = "harness"
CLASSES = (HOUSEHOLD, HARNESS)

#: panel ids of harness / test traffic (the same list the struggle detector always skipped)
SYNTHETIC_PANELS = ("replay", "test", "probe", "ci-", "e2e", "bench")
#: session ids the harnesses mint (``replay-<hex12>``, the bar's ``test-*`` / ``probe-*`` sessions, CI)
SYNTHETIC_SESSIONS = ("replay-", "test-", "test_", "probe-", "probe_", "ci-", "e2e-", "e2e_", "bench-", "bench_", "harness-")
_HINTS = frozenset({"harness", "probe", "test", "replay", "bench"})
_TRUE = frozenset({"1", "true", "yes", "on"})


def _guest(user_id: str) -> bool:
    u = (user_id or "").strip().lower()
    return u in ("", "guest", "anonymous", "voice-guest", "voice-daemon") or u.startswith("guest-")


def classify(user_id: Optional[str], session_id: Optional[str] = "", panel_id: Optional[str] = "", *,
             hint: Optional[str] = None) -> str:
    """``household`` or ``harness``. Pure apart from one env read; never raises."""
    try:
        if (os.environ.get("ZOE_HARNESS") or "").strip().lower() in _TRUE:
            return HARNESS
        if (hint or "").strip().lower() in _HINTS:
            return HARNESS
        pid = (panel_id or "").strip().lower()
        if pid and pid.startswith(SYNTHETIC_PANELS):
            return HARNESS
        sid = (session_id or "").strip().lower()
        if sid.startswith(SYNTHETIC_SESSIONS):
            return HARNESS
        uid = str(user_id or "")
        if not _guest(uid):
            from user_filters import is_synthetic_user

            if is_synthetic_user(uid):
                return HARNESS
        return HOUSEHOLD
    except Exception:  # noqa: BLE001 - when in doubt it is NOT household: harness rows never train
        return HARNESS
