"""Base class / interface for all proactive triggers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class TriggerResult:
    """What a trigger wants to send."""
    user_id: str
    message: str
    trigger_type: str
    item_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    #: called by the engine once ``fire_notification`` has returned for this result (the nudge is recorded, or deferred to a
    #: scheduled job), never when it raised: a trigger's own "already sent today" mark belongs here, not in ``check``
    on_delivered: Optional[Callable[[], None]] = field(default=None, repr=False, compare=False)


class ProactiveTrigger(ABC):
    """
    Abstract trigger.  Two tiers derive from this:
      - Tier 1: APScheduler-backed (reminders) — uses register_job / cancel_job.
      - Tier 2: Slow-loop checks — implements should_fire / compose.
    """

    trigger_type: str = "base"

    @abstractmethod
    async def check(self, db) -> list[TriggerResult]:
        """Return a list of TriggerResults ready to fire now."""
        ...
