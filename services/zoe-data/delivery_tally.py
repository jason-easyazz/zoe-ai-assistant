"""delivery_tally - count deliveries AS THEY HAPPEN, so an exit that skips the lane's return value still knows something got through.

``fire_notification`` used to learn "a lane delivered" only when the lane RETURNED. A cancellation (or any BaseException) arriving
after one subscription's push was sent but before the lane returned lost that fact, so the handler removed the pending row and
released the once-a-day claim and a restarted worker sent the nudge again. Each lane now calls ``note()`` the moment one delivery
has succeeded (one web-push subscription, a Telegram send, a speech enqueue, an in-app row); ``fire_notification`` opens a tally for
its own task with ``start()`` and every exit - return, cancel, timeout, error - consults the same ``Tally``. Outside a
``fire_notification`` there is no tally and ``note()`` does nothing. In-process only, ids and a count: nothing stored."""
from __future__ import annotations

import contextvars
from typing import Optional

_current: "contextvars.ContextVar[Optional[Tally]]" = contextvars.ContextVar("delivery_tally", default=None)


class Tally:
    __slots__ = ("n", "inflight")

    def __init__(self) -> None:
        self.n = 0          # deliveries that succeeded
        self.inflight = 0   # sends handed off whose outcome is not known yet

    def add(self) -> None:
        self.n += 1

    def __bool__(self) -> bool:
        """Something got through, OR a send was handed off and has not resolved (a cancel arrived mid-send). UNCERTAIN COUNTS AS
        DELIVERED: at most once, the same rule ``reminder_delivery`` applies to a transport that failed mid-send. An unprompted nudge
        said twice is worse than one that might have been missed."""
        return self.n > 0 or self.inflight > 0


def start() -> "tuple[Tally, contextvars.Token]":
    tally = Tally()
    return tally, _current.set(tally)


def stop(token: "contextvars.Token") -> None:
    _current.reset(token)


def begin() -> None:
    """A send is being handed off (called right BEFORE it, e.g. to the push executor). Pair with ``settle()`` once its outcome is
    known; a cancellation that arrives in between leaves it unsettled, which ``Tally.__bool__`` reads as uncertain = delivered."""
    tally = _current.get()
    if tally is not None:
        tally.inflight += 1


def settle() -> None:
    """The handed-off send's outcome is now known (it succeeded and was ``note()``d, or it failed cleanly)."""
    tally = _current.get()
    if tally is not None and tally.inflight > 0:
        tally.inflight -= 1


def note() -> None:
    """One delivery has just succeeded (a no-op outside a ``start()``)."""
    tally = _current.get()
    if tally is not None:
        tally.add()
