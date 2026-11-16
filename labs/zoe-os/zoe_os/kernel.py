"""The kernel: the services wired together, and the loop that feeds events to skills."""
from __future__ import annotations

import itertools
import time
from typing import Callable

from .attention import Attention
from .capability import Enforcer, Grant, GrantStore, Person
from .events import Event, Kind, Visibility
from .explain import Explainer
from .provenance import subject_of
from .retention import POLICY, Retention
from .receipts import ClaimAuditor, Dispatcher, Receipt
from .spine import KERNEL, EventCache, Refused, Spine


class Context:
    """What a skill is handed with each event: publish, act, and the clock. Nothing else."""

    def __init__(self, kernel: "Kernel", skill: str) -> None:
        self.kernel, self.skill = kernel, skill
        self.cache = EventCache(lambda: kernel.spine.replay(skill))

    def now(self) -> float:
        return self.kernel.clock()

    def forgotten_subject(self, notice: Event) -> str | None:
        return subject_of(notice, self.kernel.grants.people, self.kernel.spine._owner)

    def live_seqs(self, kinds, after: int) -> set[int]:
        """The same answer ``Client.live_seqs`` gives a remote skill."""
        return {e.seq for e in self.kernel.spine.replay(self.skill, after, kinds=kinds)}

    def purge(self, action: str, args: dict, notice: Event) -> None:
        self.kernel.actions.purge(self.skill, action, args, notice)

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
        self.grants.record_live = lambda seq: seq is not None and self.spine._get(seq) is not None
        # grant ids must not restart at g1: a receipt names the grant it ran under
        self.grants._ids = itertools.count(1 + self.spine._db.execute(
            "SELECT COUNT(*) FROM events WHERE kind = 'grant_change'").fetchone()[0])
        self.actions = Dispatcher(self.spine, self.enforcer)
        self.recovered = self.actions.recover()     # intents a dead kernel left open: uncertain
        self.auditor = ClaimAuditor(self.spine, self.enforcer)
        self.attention = Attention(self.spine, self.grants.people)
        self.explainer = Explainer(self.spine, self.grants.people)
        self.retention = Retention(self.spine, POLICY)      # the chosen policy; sweeps only when called
        self._skills: list[tuple[object, object, Context]] = []
        self._store_purges: dict[str, tuple[Callable, Callable]] = {}
        self.spine._before_forget = self._purge_stores

    def register_store_purge(self, name: str, purge: Callable, empty: Callable) -> None:
        """Trusted bootstrap only: idempotent erasure and independent read-back.

        Drivers receive (subject, erased events) to match requests across owners.
        Whole-member erasure additionally calls (subject, None) for orphaned rows.
        Registration and these callbacks are never handed to skills.
        """
        if name in self._store_purges:
            raise ValueError("store purge already registered")
        self._store_purges[name] = (purge, empty)

    def _purge_stores(self, subject: str, dead: list[int], whole: bool) -> None:
        """Under the forget lock/transaction, BEFORE grants and source rows vanish.

        No ordinary action, receipt, grant override or reusable authority is created.
        A failed purge/read-back aborts forget; retry is safe even after partial
        external erasure or a crash (external stores cannot share SQLite's commit).
        """
        # A request expired to its retention skeleton has no readable body, but its seq is
        # what store rows are keyed by: hand it over as a body-less skeleton, never drop it.
        events = self.spine._erased_events(dead)
        for purge, empty in self._store_purges.values():
            # Explicit closure reaches derived rows under EVERY owner. A whole-person
            # sweep additionally reaches retained store rows whose source log expired.
            purge(subject, events)
            if empty(subject, events) is not True:
                raise Refused("external store purge unverified")
            if whole:
                purge(subject, None)
                if empty(subject, None) is not True:
                    raise Refused("external store purge unverified")

    def _grant_changed(self, change: str, g: Grant) -> None:
        record = self.spine._kernel_append(Event(
            Kind.GRANT_CHANGE, KERNEL, visibility=Visibility.KERNEL,
            payload={"change": change, "grant_id": g.id, "skill": g.skill, "person": g.person,
                     "guardian": g.guardian}))
        g.record_seq = record.seq

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
                    sub.ack(ev.seq)   # only after handling: replay an interrupted purge on restart
                except Refused:
                    pass            # already recorded on the spine as a REFUSAL event
            if not progressed:
                break
        return handled

    def close(self) -> None:
        self.spine.close()
