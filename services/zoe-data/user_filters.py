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


# The ONLY ids an internal (non-admin) caller may hard-forget through
# ``POST /api/memories/users/{id}/forget-synthetic``: the exact shapes the
# harnesses MINT — ``demo``/``test``, a family tag, then a lowercase hex nonce
# (``scripts/perf/samantha_bar.py`` mints ``demo_bar_<8 hex>``,
# ``scripts/maintenance/chroma_migrate_rehearsal.py`` ``demo_b08_<8 hex>``).
# NEVER a bare prefix: Zoe Auth derives account ids from usernames, so a real
# account can be ``demo_user`` or ``test-jason``. Deliberately NARROWER than
# SYNTHETIC_USER_RE (case-sensitive; no ``probe``/``ci``/``e2e``/``bench``).
# The route ALSO refuses any id that is a registered account. Pinned by
# tests/test_memory_forget_synthetic.py.
FORGET_SYNTHETIC_RE = re.compile(r"^(demo|test)_[a-z0-9]{1,16}_[0-9a-f]{6,32}$")


def synthetic_forget_refusal(user_id: str | None) -> str | None:
    """Why ``user_id`` may NOT be hard-forgotten by an internal caller, or None.

    Refuses guest sentinels, anything not harness-shaped (``FORGET_SYNTHETIC_RE``:
    ``demo_<tag>_<hex>`` / ``test_<tag>_<hex>``), ids with
    surrounding whitespace, and ids the operator re-admitted through
    ``ZOE_SYNTHETIC_USER_ALLOWLIST`` — an allowlisted id is treated as a real
    user, so only an admin can erase it.
    """
    uid = user_id or ""
    if uid.strip() in GUEST_USERS:
        return "guest sentinel ids are never erasable"
    if uid != uid.strip():
        return "id has surrounding whitespace"
    if not FORGET_SYNTHETIC_RE.match(uid):
        return ("not a harness-minted synthetic id (demo_<tag>_<hex> / test_<tag>_<hex>) — "
                "real users need the admin forget")
    if not is_synthetic_user(uid):
        return "id is allowlisted (ZOE_SYNTHETIC_USER_ALLOWLIST) and treated as a real user"
    return None


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
