"""The event schema: one typed, private-by-default record for everything that happens.

A CLOSED set of kinds (an unknown kind is a ValueError, not a new string), one subject
person, a visibility, a source, and a sequence number the spine assigns. Pure data: no I/O.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Kind(str, Enum):
    UTTERANCE = "utterance"              # someone spoke
    TURN_END = "turn_end"                # a turn finished
    PRESENCE = "presence"                # arrived / left / identified, with a confidence
    CALENDAR_CHANGE = "calendar_change"
    TIMER_FIRED = "timer_fired"
    REMINDER_FIRED = "reminder_fired"
    SENSOR = "sensor"
    ROUTINE_STEP = "routine_step"
    OFFER = "offer"                      # a skill proposes; it never acts
    OFFER_RESPONSE = "offer_response"    # the person accepted or declined
    CLAIM = "claim"                      # "I did X": must be backed by a receipt
    # Kernel-only kinds: no skill or adapter may publish these.
    ACTION_RECEIPT = "action_receipt"
    REFUSAL = "refusal"
    FORGET = "forget"
    GRANT_CHANGE = "grant_change"
    AUDIT_FINDING = "audit_finding"
    ATTENTION = "attention"              # how an offer was routed to a person, and why


KERNEL_ONLY = frozenset(
    {Kind.ACTION_RECEIPT, Kind.REFUSAL, Kind.FORGET, Kind.GRANT_CHANGE, Kind.AUDIT_FINDING,
     Kind.ATTENTION}
)


class Visibility(str, Enum):
    PRIVATE = "private"        # the subject's alone: only a reader the SUBJECT granted
    HOUSEHOLD = "household"    # any reader an adult of the household granted
    KERNEL = "kernel"          # audit trail: only a reader granted kernel visibility


# Payload keys each kind must carry. Small on purpose: the schema is the contract.
REQUIRED_KEYS: dict[Kind, tuple[str, ...]] = {
    Kind.UTTERANCE: ("text",),
    Kind.PRESENCE: ("state", "confidence"),
    Kind.CALENDAR_CHANGE: ("event_id", "change"),
    Kind.ROUTINE_STEP: ("routine", "item", "done"),
    Kind.OFFER: ("offer_id", "text"),
    Kind.OFFER_RESPONSE: ("offer_id", "response"),
    Kind.CLAIM: ("action",),
}

# Payload keys that hold free text a person produced. The forget shield (spine.forget)
# remembers a keyed hash of these values so a skill cannot re-publish them from a cache.
CONTENT_KEYS = frozenset({"text", "title", "item", "note"})


@dataclass(frozen=True)
class Event:
    kind: Kind
    source: str
    subject: str | None = None
    visibility: Visibility = Visibility.PRIVATE
    payload: dict[str, Any] = field(default_factory=dict)
    mentions: tuple[str, ...] = ()      # other people this event is about
    caused_by: tuple[int, ...] = ()     # sequence numbers this event derives from
    seq: int = 0                        # assigned by the spine; 0 = not yet appended
    ts: float = 0.0                     # assigned by the spine

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", Kind(self.kind))
        object.__setattr__(self, "visibility", Visibility(self.visibility))
        object.__setattr__(self, "mentions", tuple(self.mentions))
        object.__setattr__(self, "caused_by", tuple(int(c) for c in self.caused_by))
        if not self.source or not isinstance(self.source, str):
            raise ValueError("an event needs a source")
        if self.visibility is Visibility.PRIVATE and not self.subject:
            raise ValueError("a private event needs a subject: private to whom?")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dict")
        missing = [k for k in REQUIRED_KEYS.get(self.kind, ()) if k not in self.payload]
        if missing:
            raise ValueError(f"{self.kind.value} payload is missing {missing}")
        json.dumps(self.payload)  # must be JSON: raises TypeError otherwise

    @property
    def people(self) -> frozenset[str]:
        """Everyone this event is about: the subject plus anyone mentioned."""
        return frozenset(p for p in (self.subject, *self.mentions) if p)
