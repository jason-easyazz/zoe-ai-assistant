"""Capability and consent: what a skill declares, what each person granted, one enforcer.

* A MANIFEST is what a skill says it needs: which event kinds it reads and how private,
  which kinds it emits, which named actions it performs. It is a ceiling.
* A GRANT is one person saying yes to one skill, for all or part of its manifest. No grant,
  no access (default deny). A child's grant needs a guardian's signature. A guest cannot
  grant. A grant can be revoked at any time and the next delivery already obeys it.
* The ENFORCER is the single decision point. The spine asks it before every publish,
  subscribe and delivery; the action dispatcher asks it before every action.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .events import KERNEL_ONLY, Event, Kind, Visibility
from .spine import Decision

ADULT, CHILD, GUEST = "adult", "child", "guest"


class ConsentError(Exception):
    """A manifest or a grant that the model does not allow."""


@dataclass(frozen=True)
class Person:
    id: str
    role: str = ADULT
    guardians: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    skill: str
    reads: Mapping[Kind, Visibility] = field(default_factory=dict)  # most private level per kind
    emits: frozenset[Kind] = frozenset()
    acts: frozenset[str] = frozenset()
    origin: bool = False   # a trusted adapter that records first-hand reality (voice, calendar)

    _KEYS = frozenset({"skill", "reads", "emits", "acts", "origin"})

    @classmethod
    def from_dict(cls, raw: Mapping) -> "Manifest":
        """Parse the declared format. Unknown keys, kinds or levels are errors, not warnings."""
        unknown = set(raw) - cls._KEYS
        if unknown:
            raise ConsentError(f"unknown manifest keys {sorted(unknown)}")
        try:
            reads = {Kind(k): Visibility(v) for k, v in dict(raw.get("reads", {})).items()}
            emits = frozenset(Kind(k) for k in raw.get("emits", ()))
        except ValueError as exc:
            raise ConsentError(str(exc)) from None
        if emits & KERNEL_ONLY:
            raise ConsentError("a skill cannot emit a kernel-only kind")
        if not raw.get("skill"):
            raise ConsentError("a manifest needs a skill name")
        return cls(str(raw["skill"]), reads, emits, frozenset(raw.get("acts", ())),
                   bool(raw.get("origin", False)))


@dataclass
class Grant:
    id: str
    person: str
    skill: str
    reads: Mapping[Kind, Visibility]
    acts: frozenset[str]
    guardian: str | None = None
    active: bool = True


def _covers(level: Visibility | None, wanted: Visibility) -> bool:
    """A private-level read also covers household events; nothing else crosses levels."""
    if level is None:
        return False
    return level is wanted or (level is Visibility.PRIVATE and wanted is Visibility.HOUSEHOLD)


class GrantStore:
    def __init__(self, people: list[Person],
                 on_change: Callable[[str, Grant], None] | None = None) -> None:
        self.people = {p.id: p for p in people}
        self.manifests: dict[str, Manifest] = {}
        self._grants: dict[str, dict[str, Grant]] = {}     # skill -> person -> grant
        self._ids = itertools.count(1)
        self.on_change = on_change

    def install(self, manifest: Manifest | Mapping) -> Manifest:
        m = manifest if isinstance(manifest, Manifest) else Manifest.from_dict(manifest)
        self.manifests[m.skill] = m
        return m

    def grant(self, person: str, skill: str, *, reads: Mapping | None = None,
              acts=None, guardian: str | None = None) -> Grant:
        """One person's yes. ``reads`` / ``acts`` default to everything the manifest asks."""
        who, m = self.people.get(person), self.manifests.get(skill)
        if who is None or m is None:
            raise ConsentError("unknown person or skill")
        if who.role == GUEST:
            raise ConsentError("a guest cannot grant")
        if who.role == CHILD:
            signer = self.people.get(guardian or "")
            if signer is None or signer.role != ADULT or signer.id not in who.guardians:
                raise ConsentError("a child's grant needs one of their guardians")
        reads = dict(m.reads) if reads is None else {Kind(k): Visibility(v) for k, v in reads.items()}
        acts = m.acts if acts is None else frozenset(acts)
        for kind, level in reads.items():
            if not (m.reads.get(kind) is level or _covers(m.reads.get(kind), level)):
                raise ConsentError("a grant cannot exceed the manifest")
        if not acts <= m.acts:
            raise ConsentError("a grant cannot exceed the manifest")
        g = Grant(f"g{next(self._ids)}", person, skill, reads, acts, guardian)
        self._grants.setdefault(skill, {})[person] = g
        if self.on_change:
            self.on_change("granted", g)
        return g

    def revoke(self, person: str, skill: str) -> bool:
        g = self._grants.get(skill, {}).pop(person, None)
        if g is None:
            return False
        g.active = False
        if self.on_change:
            self.on_change("revoked", g)
        return True

    def of(self, skill: str, person: str | None) -> Grant | None:
        return self._grants.get(skill, {}).get(person or "")

    def all_for(self, skill: str) -> list[Grant]:
        return list(self._grants.get(skill, {}).values())


class Enforcer:
    """The one policy decision point. Implements the spine's Authorizer and ``may_act``."""

    def __init__(self, store: GrantStore) -> None:
        self.store = store

    def is_origin(self, source: str) -> bool:
        m = self.store.manifests.get(source)
        return bool(m and m.origin)

    def may_publish(self, source: str, event: Event) -> Decision:
        m = self.store.manifests.get(source)
        if m is None:
            return Decision(False, "no manifest")
        if event.kind not in m.emits:
            return Decision(False, "asks for more than its manifest")
        if m.origin:
            return Decision(True)
        if event.subject:
            g = self.store.of(source, event.subject)
            return Decision(True, grant_id=g.id) if g else Decision(False, "no grant from that person")
        return Decision(bool(self.store.all_for(source)), "no grant")

    def may_subscribe(self, reader: str, kinds, subject: str | None) -> Decision:
        m = self.store.manifests.get(reader)
        if m is None:
            return Decision(False, "no manifest")
        if any(k not in m.reads and k is not Kind.FORGET for k in kinds or ()):
            return Decision(False, "asks for more than its manifest")
        if subject and self.store.of(reader, subject) is None:
            return Decision(False, "no grant from that person")
        return Decision(True)

    def may_read(self, reader: str, event: Event) -> Decision:
        m = self.store.manifests.get(reader)
        if m is None or not _covers(m.reads.get(event.kind), event.visibility):
            return Decision(False, "not in manifest")
        if event.visibility is Visibility.PRIVATE:
            g = self.store.of(reader, event.subject)
            ok = g is not None and g.reads.get(event.kind) is Visibility.PRIVATE
            return Decision(True, grant_id=g.id) if ok else Decision(False, "no grant from the subject")
        for g in self.store.all_for(reader):    # household / kernel: any member's grant that covers it
            if _covers(g.reads.get(event.kind), event.visibility):
                return Decision(True, grant_id=g.id)
        return Decision(False, "no grant")

    def may_act(self, skill: str, action: str, on_behalf_of: str) -> Decision:
        m = self.store.manifests.get(skill)
        if m is None:
            return Decision(False, "no manifest")
        if action not in m.acts:
            return Decision(False, "asks for more than its manifest")
        g = self.store.of(skill, on_behalf_of)
        if g is None or action not in g.acts:
            return Decision(False, "no grant from that person")
        return Decision(True, grant_id=g.id)

    def may_forget(self, actor: str, subject: str) -> Decision:
        """A person may forget their own events; a guardian may for their child. Never a skill."""
        who = self.store.people.get(subject)
        if who is None or actor not in self.store.people:
            return Decision(False, "only a person can ask to forget")
        return Decision(actor == subject or actor in who.guardians, "not theirs to forget")
