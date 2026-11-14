"""The attention budget: when an offer may reach a person, by which channel, how often.

A skill never chooses the channel. It publishes an OFFER; the surface that would show or
speak it asks ``Attention.route`` first. Unprompted speech is OFF for everyone by default
and is a per-person setting only that person (or a child's guardian) can change. The
default channel is a silent card. Past the daily budget an offer is held for the digest.
Every routing decision is appended to the spine, so "why did she say that?" has an answer.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .capability import ADULT, CHILD, ConsentError, Person
from .events import Event, Kind
from .spine import KERNEL, Spine

CARD, SPOKEN, DIGEST = "card", "spoken", "digest"


@dataclass(frozen=True)
class AttentionSetting:
    spoken_unprompted: bool = False     # the owner's standing choice is OFF
    offers_per_day: int = 3
    quiet_from_hour: int = 22
    quiet_to_hour: int = 7


class Attention:
    def __init__(self, spine: Spine, people: dict[str, Person]) -> None:
        self.spine, self.people = spine, people
        self._settings: dict[str, AttentionSetting] = {}
        self._spent: dict[tuple[str, int], int] = {}

    def setting(self, person: str) -> AttentionSetting:
        return self._settings.get(person, AttentionSetting())

    def set(self, person: str, *, by: str, **changes) -> AttentionSetting:
        """Only the person, or a guardian for a child, may change how Zoe reaches them."""
        who = self.people.get(person)
        if who is None:
            raise ConsentError("unknown person")
        own = by == person and who.role == ADULT
        guardian = who.role == CHILD and by in who.guardians
        if not (own or guardian):
            raise ConsentError("not theirs to change")
        self._settings[person] = replace(self.setting(person), **changes)
        return self._settings[person]

    def route(self, offer: Event, *, hour: int, day: int) -> str:
        """Decide the channel for one offer and record why."""
        person = offer.subject or ""
        s = self.setting(person)
        quiet = (hour >= s.quiet_from_hour or hour < s.quiet_to_hour) \
            if s.quiet_from_hour > s.quiet_to_hour else s.quiet_from_hour <= hour < s.quiet_to_hour
        spent = self._spent.get((person, day), 0)
        if spent >= s.offers_per_day:
            channel, reason = DIGEST, "daily budget spent"
        elif quiet:
            channel, reason = DIGEST, "quiet hours"
        elif s.spoken_unprompted:
            channel, reason = SPOKEN, "this person turned unprompted speech on"
        else:
            channel, reason = CARD, "unprompted speech is off for this person"
        if channel != DIGEST:
            self._spent[(person, day)] = spent + 1
        self.spine._kernel_append(Event(
            Kind.ATTENTION, KERNEL, subject=person, caused_by=(offer.seq,),
            payload={"channel": channel, "reason": reason}))
        return channel
