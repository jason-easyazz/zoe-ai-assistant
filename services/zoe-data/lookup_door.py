"""The Zoe OS door for LOOKUPS (``ZOE_OS_LOOKUPS=kernel``; default off; docs/knowledge/zoe-os-lookups-messages-door.md).

A lookup (the weather, a web search, a page fetch) is read-only for the household but SENDS DATA OFF THE BOX. There is
nothing to permit, so the kernel is never asked first and a lookup is never delayed. Two calls, at two places:

* ``check(kind)`` at each ENTRY point, before anything can leave: it records nothing. Its one job is the refusal below.
* ``sent(kind)`` right AFTER each outbound request, at the line that sent it (the provider answered, or the page loaded): the
  kernel is TOLD then (``os_shadow.record_door``): who, and the class. A lookup that never went out (an empty query, a
  cache hit, a provider that is off or not installed, a URL refused as private) is never recorded. A request that failed
  before an answer came back (DNS, a refused connection, a timeout) is not recorded either: what is recorded is a lookup
  that reached its provider. Never the query, the place, the URL or the result.

WHO is looking is the request's or the turn's (``kernel_door.acting``, noted by every authenticated entry point). Nobody
noted = automatic (the wall panel's weather card, a night job's fetch): not recorded. One record per class per request,
however many fetches a search fans out into. A sender on an executor thread is run through ``carry`` (``run_in_executor``
does not copy the context, so the thread would not know who is asking).

ONE refusal, made by ``check`` with no kernel call: a caller zoe-data KNOWS is not on the kernel's roster (the roster as last
read into memory) is refused, because the lookup would leave the box in their name. A roster not read yet refuses nobody.

There is no child rule for the web in zoe-data today (no safe-search, no per-child block), so there is none here.
"""
from __future__ import annotations

import contextvars
import functools

import kernel_door
import os_shadow

AREA = "lookups"
WEATHER, WEB_SEARCH, WEB_FETCH = "weather", "web_search", "web_fetch"
KINDS = (WEATHER, WEB_SEARCH, WEB_FETCH)
#: (the acting tuple of this request, the classes already recorded for it)
_DONE: contextvars.ContextVar = contextvars.ContextVar("zoe_lookup_door_done", default=None)


class LookupRefused(kernel_door.Refused):
    """Nothing was sent: the caller is not someone the kernel knows."""

    area = AREA
    lines = {"unknown_person": "I don't know who is asking, so I haven't looked that up."}
    fallback = "I can't look that up for you."


def check(kind: str) -> None:
    """At an entry point, before anything can leave the box. Records nothing, never waits. Raises ``LookupRefused`` only for
    a caller known to be off the roster."""
    if not os_shadow.area_on(AREA):
        return
    who = kernel_door.acting()
    if who is None:
        return
    if not kernel_door.is_panel(who[0]):
        known = os_shadow._roster_seen()
        if known is not None and who[0] not in known:
            os_shadow.door_unchecked(f"lookup.{kind}", "unknown_person", AREA)
            raise LookupRefused("unknown_person")
    _done(who)          # this request's set, made HERE so a sender on a carried thread adds to the same one


def sent(kind: str) -> None:
    """Right AFTER an outbound request of this class was answered. Never waits, never raises, never refuses (the refusal is
    ``check``'s, before it left): tells the kernel once per class per request."""
    if not os_shadow.area_on(AREA):
        return
    who = kernel_door.acting()
    if who is None:
        return
    done = _done(who)
    if kind in done[1]:
        return
    done[1].add(kind)
    actor, stamps_at = who
    panel = kernel_door.is_panel(actor)
    os_shadow.record_door(AREA, f"lookup.{kind}", "" if panel else actor, panel=panel, household=True,
                          stamps_at=stamps_at if stamps_at is not None else os_shadow.stamp_snapshot())


def carry(fn):
    """``fn`` for ``run_in_executor``, carrying who is asking onto the thread. Door off: ``fn`` itself (the same call as before)."""
    if not os_shadow.area_on(AREA):
        return fn
    return functools.partial(contextvars.copy_context().run, fn)


def _done(who) -> tuple:
    done = _DONE.get()
    if done is None or done[0] is not who:
        done = (who, set())
        _DONE.set(done)
    return done
