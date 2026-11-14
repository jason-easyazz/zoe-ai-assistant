"""The kernel: the services wired together, and the loop that feeds events to skills."""
from __future__ import annotations

import itertools
import time
from typing import Callable

from .attention import Attention
from .capability import Enforcer, Grant, GrantStore, Person
from .events import Event, Kind, Visibility
from .receipts import ClaimAuditor, Dispatcher, Receipt
from .spine import KERNEL, Refused, Spine


class Context:
    """What a skill is handed with each event: publish, act, and the clock. Nothing else."""

    def __init__(self, kernel: "Kernel", skill: str) -> None:
        self.kernel, self.skill = kernel, skill

    def now(self) -> float:
        return self.kernel.clock()

    def publish(self, kind: Kind, *, subject: str | None, payload: dict,
                caused_by: tuple[int, ...], visibility: Visibility = Visibility.PRIVATE) -> Event:
        return self.kernel.spine.publish(Event(kind, self.skill, subject, visibility, payload,
                                               caused_by=caused_by))

    def act(self, action: str, on_behalf_of: str, args: dict, *,
            caused_by: tuple[int, ...]) -> Receipt:
        return self.kernel.actions.act(self.skill, action, on_behalf_of, args, caused_by=caused_by)


class Kernel:
    def __init__(self, path: str, people: list[Person], *, queue_size: int = 256,
                 clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.grants = GrantStore(people, on_change=self._grant_changed)
        self.enforcer = Enforcer(self.grants)
        self.spine = Spine(path, self.enforcer, queue_size=queue_size, clock=clock)
        # grant ids must not restart at g1: a receipt names the grant it ran under
        self.grants._ids = itertools.count(1 + self.spine._db.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'grant_change'").fetchone()[0])
        self.actions = Dispatcher(self.spine, self.enforcer)
        self.recovered = self.actions.recover()     # intents a dead kernel left open: uncertain
        self.auditor = ClaimAuditor(self.spine, self.enforcer)
        self.attention = Attention(self.spine, self.grants.people)
        self._skills: list[tuple[object, object, Context]] = []

    def _grant_changed(self, change: str, g: Grant) -> None:
        self.spine._kernel_append(Event(
            Kind.GRANT_CHANGE, KERNEL, visibility=Visibility.KERNEL,
            payload={"change": change, "grant_id": g.id, "skill": g.skill, "person": g.person,
                     "guardian": g.guardian}))

    def install(self, skill) -> None:
        """Register a skill's manifest and subscribe it to exactly what the manifest reads."""
        m = self.grants.install(skill.manifest)
        sub = self.spine.subscribe(m.skill, kinds=set(m.reads)) if m.reads else None
        self._skills.append((skill, sub, Context(self, m.skill)))

    def pump(self, max_rounds: int = 1000) -> int:
        """Deliver pending events to every skill until all are idle. Returns events handled."""
        handled = 0
        for _ in range(max_rounds):
            progressed = False
            for skill, sub, ctx in self._skills:
                ev = sub.poll() if sub is not None else None
                if ev is None:
                    continue
                progressed, handled = True, handled + 1
                try:
                    skill.on_event(ev, ctx)
                except Refused:
                    pass            # already recorded on the spine as a REFUSAL event
            if not progressed:
                break
        return handled

    def close(self) -> None:
        self.spine.close()
