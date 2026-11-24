"""Who is acting on a memory row: a household MEMBER, or nobody (an AUTOMATIC writer).

Every mutating ``MemoryService`` method takes ``acting=`` with NO default, so each call site says which it is
(``tests/memory_writers.py`` is the list; ``tests/test_memory_one_writer.py`` checks every site against it):

  * ``memory_actor.member(user_id)``  a member asked for this change (spoken, typed, tapped, or a tool the brain called for
    them). Build it as the FIRST statement of the entry point, before anything is awaited: it carries the erase stamps of
    that moment for the Zoe OS memory door (``ZOE_OS_MEMORY=kernel``; nothing is read while that flag is off).
  * ``memory_actor.AUTOMATIC``        no member asked for THIS write: a per-turn extractor, a night pass, a mirror of a change
    another area's door already decided, an eraser finishing an earlier forget, a seeder. Never asked, never recorded.

``acting`` is WHO ASKED, not whose row it is (a curator acting on a member's row is the curator) and not the audit label
(the methods' older ``actor=`` string, which automatic passes fill too).

Stdlib only at import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union


@dataclass(frozen=True)
class Member:
    """The member who asked. ``stamps``: the erase-stamp snapshot taken when they asked (None while the door is off)."""

    user_id: str
    stamps: Any = None


class _Automatic:
    __slots__ = ()

    def __repr__(self) -> str:
        return "AUTOMATIC"


AUTOMATIC = _Automatic()
Acting = Union[Member, _Automatic]


def member(user_id: Any) -> Member:
    """The acting member, with the erase stamps of NOW (call it before the first await of the entry point)."""
    import kernel_door

    return Member(str(user_id or ""), kernel_door.stamps_now("memory"))


def check(acting: Any) -> Acting:
    """``acting`` as given, or a ``TypeError``: a writer that does not say who is acting is a bug, never a default."""
    if isinstance(acting, Member) or acting is AUTOMATIC:
        return acting
    raise TypeError("acting= must be memory_actor.member(...) or memory_actor.AUTOMATIC")
