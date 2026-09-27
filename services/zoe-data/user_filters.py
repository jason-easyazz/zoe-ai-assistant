"""Who counts as a real user for the batch memory passes and proactive triggers.

Harnesses and probes have written synthetic ids (``test-sec-b-*``,
``test-route-probe``, ``demo-*``) to the live chat tables; 21 of the 25 ids the
dreaming pass walked on 2026-09-27 were synthetic. Stdlib-only so ``proactive/``
can import it without pulling ``memory_digest``.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable

# Sentinel owners that are never a person. The shared kiosk account ``guest`` and
# the voice daemon identities stand in for "nobody identified".
GUEST_USERS: tuple[str, ...] = ("guest", "anonymous", "voice-guest", "voice-daemon", "")

# Anchored on a separator so a real name that merely STARTS with these letters
# ("Christine", "Benchley", "Testa") cannot match: ``ci`` needs ``ci-``/``ci_``.
SYNTHETIC_USER_RE = re.compile(r"^(test|probe|demo|ci|e2e|bench)[-_]", re.IGNORECASE)


def synthetic_user_allowlist() -> frozenset[str]:
    """Ids that match the synthetic pattern but must still be processed.

    Read per call (cheap) so a flag flip needs no restart and tests can
    monkeypatch it. Comma-separated; blanks ignored. Default empty.
    """
    raw = os.environ.get("ZOE_SYNTHETIC_USER_ALLOWLIST", "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def is_synthetic_user(user_id: str | None) -> bool:
    """True when ``user_id`` is a sentinel or a test/demo/probe id.

    Guest sentinels are ALWAYS excluded: the allowlist only overrides the
    synthetic-name pattern (a lab opting a ``demo_*`` user back in), it can never
    turn the shared kiosk account into a person.
    """
    uid = (user_id or "").strip()
    if uid in GUEST_USERS:
        return True
    if not SYNTHETIC_USER_RE.match(uid):
        return False
    return uid not in synthetic_user_allowlist()


def drop_synthetic_users(
    user_ids: Iterable[str | None], *, pass_name: str, log: logging.Logger | None = None,
) -> list[str]:
    """Filter ``user_ids`` to real users and log ONE line with the skipped count.

    Order is preserved and duplicates are kept as given (callers already
    de-duplicate in SQL). The log line carries counts only — never ids.
    """
    kept: list[str] = []
    skipped = 0
    for uid in user_ids:
        if is_synthetic_user(uid):
            skipped += 1
        else:
            kept.append(uid)  # type: ignore[arg-type]
    (log or logging.getLogger(__name__)).info(
        "%s: users kept=%d skipped_synthetic=%d", pass_name, len(kept), skipped
    )
    return kept


def message_owner_expr() -> str:
    """SQL expression for the owner of a ``chat_messages cm JOIN chat_sessions cs`` row.

    The per-turn ``metadata.user_id`` wins (a kiosk session is ``guest``-owned but
    each turn names the identified speaker); the session owner is the fallback;
    guest sentinels resolve to NULL.
    """
    guests = ", ".join("'" + user.replace("'", "''") + "'" for user in GUEST_USERS)
    # chat_messages.metadata is TEXT and legacy rows may contain non-JSON.
    # Extract the simple {"user_id": "..."} field without a jsonb cast so one
    # malformed row cannot fail discovery for every user.
    metadata_user = (
        "CASE WHEN cm.metadata ~ '^\\s*\\{' "
        "THEN substring(cm.metadata from '\"user_id\"\\s*:\\s*\"([^\"]+)\"') "
        "ELSE NULL END"
    )
    return (
        "CASE "
        f"WHEN COALESCE({metadata_user}, '') NOT IN ({guests}) "
        f"THEN {metadata_user} "
        f"WHEN COALESCE(cs.user_id, '') NOT IN ({guests}) "
        "THEN cs.user_id "
        "ELSE NULL END"
    )
