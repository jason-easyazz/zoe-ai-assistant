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
    __slots__ = ("n",)

    def __init__(self) -> None:
        self.n = 0

    def add(self) -> None:
        self.n += 1

    def __bool__(self) -> bool:
        return self.n > 0


def start() -> "tuple[Tally, contextvars.Token]":
    tally = Tally()
    return tally, _current.set(tally)


def stop(token: "contextvars.Token") -> None:
    _current.reset(token)


def note() -> None:
    """One delivery has just succeeded (a no-op outside a ``start()``)."""
    tally = _current.get()
    if tally is not None:
        tally.add()
