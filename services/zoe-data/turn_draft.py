"""A brain-turn block computed now, with its state changes held until the turn reaches the POST.

Some context blocks advance state when they are built: a per-session turn counter (``voice_session_bridge``,
``conversation_notes`` ``_YOUNG``), a cooldown clock and a persisted served count (``conversation_notes``). Built
sequentially, they run just before the POST, so an interrupted turn rarely burns them. Prefetched beside the precedence
chain (``zoe_flue_client`` ZOE_FLUE_PREPOST_CONCURRENT), they would run for a turn that later aborts. A ``TurnDraft`` is
the pure read (``block``) plus the deferred mutations (``on_commit``); ``commit()`` applies them once, in order. Never
raises: a failing mutation is logged and the next one still runs (each was best-effort before).
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TurnDraft:
    __slots__ = ("block", "_ops", "_done")

    def __init__(self, block: str = "") -> None:
        self.block = block
        self._ops: "list[Callable[[], Any]]" = []
        self._done = False

    def on_commit(self, op: "Callable[[], Any]") -> None:
        """Queue a mutation (a zero-arg callable; it may return an awaitable)."""
        self._ops.append(op)

    @property
    def committed(self) -> bool:
        return self._done

    async def commit(self) -> None:
        """Apply the queued mutations once, in order. A second call is a no-op. Never raises."""
        if self._done:
            return
        self._done = True
        for op in self._ops:
            try:
                r = op()
                if inspect.isawaitable(r):
                    await r
            except Exception as exc:  # noqa: BLE001 - each mutation was best-effort before; the next still runs
                logger.debug("turn draft: a commit step failed (%s)", type(exc).__name__)
