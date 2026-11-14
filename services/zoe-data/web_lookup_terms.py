"""What the household holds private, for the web-lookup query scrubber.

``household_private_terms()`` returns every member's name and every contact's
name, household-wide (the brain's ``web_search`` call carries no user
identity, and one member's contact is no more public for another). ``None``
means the names could not be read: the caller must then REFUSE the lookup -
a scrubber that does not know the names cannot vouch for a query.

Not covered here (see docs/knowledge/web-lookup.md, acceptance list): places
and the memory texts in play for the turn. The scrubber's structural rules
(street addresses, coordinates, numbers) still apply without them.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from web_lookup_scrub import PrivateTerms

logger = logging.getLogger(__name__)

CACHE_S = 60.0
_CACHE: dict[str, Any] = {"at": -1e9, "terms": None}
_clock = time.monotonic


async def household_private_terms() -> PrivateTerms | None:
    """Member + contact names (cached ``CACHE_S``; only a successful read is cached), or ``None`` when unreadable."""
    now = _clock()
    if _CACHE["terms"] is not None and now - float(_CACHE["at"]) <= CACHE_S:
        return _CACHE["terms"]
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            users = await db.fetch("SELECT name FROM users")
            people = await db.fetch("SELECT name FROM people WHERE deleted = 0 OR deleted IS NULL")
    except Exception as exc:  # noqa: BLE001 - fail closed: no names, no lookup
        logger.warning("web_lookup: private names unreadable (%s) - lookups are refused", type(exc).__name__)
        return None
    terms = PrivateTerms.of(names=[str(r["name"] or "").strip() for r in list(users) + list(people)])
    _CACHE.update(at=now, terms=terms)
    return terms
