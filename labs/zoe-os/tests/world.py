"""A synthetic household for the skill tests. Invented people, invented words."""
from __future__ import annotations

from datetime import datetime, timezone

from zoe_os.capability import CHILD, Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.kernel import Kernel

ALEX, SAM, KID = "alex", "sam", "kid"
NOW = datetime(2030, 3, 4, 18, 0, tzinfo=timezone.utc).timestamp()     # a fixed "today"
TOMORROW_0630 = datetime(2030, 3, 5, 6, 30, tzinfo=timezone.utc).timestamp()
TOMORROW_1100 = datetime(2030, 3, 5, 11, 0, tzinfo=timezone.utc).timestamp()

ADAPTERS = [   # trusted origin adapters: they record first-hand reality and read nothing
    {"skill": "voice", "emits": ["utterance"], "origin": True},
    {"skill": "presence", "emits": ["presence"], "origin": True},
    {"skill": "routines", "emits": ["routine_step"], "origin": True},
    {"skill": "calendar", "emits": ["calendar_change"], "origin": True},
    {"skill": "panel", "emits": ["offer_response"], "origin": True},
]
AUDIT = {"skill": "audit", "reads": {k: "kernel" for k in
                                     ("refusal", "audit_finding", "grant_change", "attention")}}


class Alarms:
    def __init__(self):
        self.at: dict[str, str] = {}

    def do(self, person, args):
        self.at[person] = args["to"]

    def verify(self, person, args, result):
        return {"alarm": self.at[person]} if self.at.get(person) == args["to"] else None

    def snapshot(self, person, args):               # what an undo would put back
        return {"alarm": self.at.get(person)}

    def undo(self, person, args, before):
        if before["alarm"] is None:
            self.at.pop(person, None)
        else:
            self.at[person] = before["alarm"]


class Lights:
    def __init__(self):
        self.toggled: list[str] = []

    def do(self, person, args):
        self.toggled.append(args["room"])

    def verify(self, person, args, result):
        return {"room": args["room"]}


def household(tmp_path):
    k = Kernel(str(tmp_path / "zoe-os.db"),
               [Person(ALEX), Person(SAM), Person(KID, CHILD, guardians=(ALEX,))],
               clock=lambda: NOW)
    k.path = str(tmp_path / "zoe-os.db")
    for m in (*ADAPTERS, AUDIT):
        k.grants.install(m)
    k.grants.grant(ALEX, "audit")
    k.alarms, k.lights = Alarms(), Lights()
    k.actions.register("alarm.move", k.alarms.do, k.alarms.verify,
                       snapshot=k.alarms.snapshot, undo=k.alarms.undo)
    k.actions.register("light.toggle", k.lights.do, k.lights.verify, needs_accept=False)
    return k


def emit(k, kind, source, person, visibility=Visibility.PRIVATE, cause=(), **payload) -> Event:
    return k.spine.publish(Event(kind, source, person, visibility, payload, caused_by=cause))


def step(k, person, item, done=False):
    return emit(k, Kind.ROUTINE_STEP, "routines", person, routine="take-with-you", item=item, done=done)


def presence(k, person, state, confidence=0.95, visibility=Visibility.PRIVATE):
    return emit(k, Kind.PRESENCE, "presence", person, visibility, state=state, confidence=confidence)


def answer(k, offer: Event, response="accepted"):
    return emit(k, Kind.OFFER_RESPONSE, "panel", offer.subject, cause=(offer.seq,),
                offer_id=offer.payload["offer_id"], response=response)


def of_kind(k, kind, reader="audit"):
    """Kernel-internal read for assertions: every live event of one kind."""
    return k.spine._fetch(0, frozenset({kind}), None, 10_000)


def refusal_reasons(k):
    return [e.payload["reason"] for e in of_kind(k, Kind.REFUSAL)]
