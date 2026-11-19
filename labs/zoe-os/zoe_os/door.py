"""The door: the kernel DECIDES and RECORDS a change to something production holds; the thing itself stays where it is.

Owner decision 2026-11-18 (``docs/architecture/zoe-os-cutover-plan.md``): for each AREA (lists, reminders, ...) production asks
before a change ("may this person do this verb to this thing?"), makes the change itself in its own store, then reports the
outcome. Nothing here holds a list or a reminder. This module is the ONE mechanism every area shares; an area is only a name, its
verbs and its permission table (``Area``; ``lists.AREA``, ``remind_door.AREA``). A new area adds a table, never a second door.

What is recorded, and what is NOT:

* an ASK that is allowed leaves one receipt event (outcome ``allowed``: the verb, the acting person, the id of the thing changed and of its parts, and
  its owner and visibility AS TOLD by the caller, plus the caller's turn id when given) and one write-ahead intent row;
* the REPORT closes that intent with a second small receipt (``done`` or ``failed``) that cites the first;
* an ask that is never reported is an open intent: the next start gives it an ``uncertain`` receipt (``Dispatcher.recover``), like
  every other action the kernel did not see finish;
* a REFUSAL is a refusal event (who asked, the verb, a fixed reason code; the thing's id only when the person is known).

IDS ONLY. No text, no name, no contents, no before-state snapshot, and no row per existing part: the cost of an
act does not grow with the thing changed (``tests/test_lists_door.py`` pins rows per act and that ``snapshot_dependencies`` stays empty).
Because no TEXT is ever stored for these verbs, a forget has nothing of the text to scrub here; the ids, the acting person and
the told owner are ordinary event fields and member links, so a person's forget reaches these records like any other.

ORDER AGAINST A FORGET (the caller's asks run on their own connection, so one can reach the kernel AFTER a later forget of a
member it names). The caller sends its erase stamp for every member an ask names (``stamps``), and its whole-person forgets carry
the same stamp (``spine.forget(stamp=...)``, kept as that person's erase mark). Under the kernel lock, an ask whose stamp for the
acting person or the owner is NOT STRICTLY NEWER than that person's mark was made before their forget was sent: it writes nothing that names
anyone (an allowed one is refused ``erased``, with a bare refusal event; a refused one keeps its reason code, unnamed). A strictly
newer stamp is recorded as usual: a forgotten member who acts again is recorded afresh (the caller moves its count on only once
this kernel has confirmed the forget, so equal means "read before that forget was confirmed"). A named member with a mark and NO stamp
(an older caller) is treated as older. A report needs no stamp to be safe (a whole-person forget removes every open ask that names
the person, as actor or owner, so there is nothing left to close), and one that carries a stale stamp is refused the same way.
Scoped forgets (entity, topic, seqs) set no mark: an ask holds ids only, so it cannot write back what they erased.

The rules are household rules by ROLE (``capability.Person.role``), not a consent a skill collected: a thing belongs to its owner
or to the house, whatever skill carried the request. The calling skill is still bounded by its manifest (it must name the verb).
Not on the roster = unknown = refused (least access), for an actor AND for an owner: an owner the kernel does not know is not a
person it could later forget, so their id is never written.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .events import Event, Kind, Visibility
from .spine import KERNEL, Decision, Refused, erase_stamp

HOUSEHOLD, PRIVATE = "household", "private"
PANEL = "panel"                         # ``via``: the shared panel acting for the house, nobody identified
OUTCOMES = ("done", "failed")
MAX_PARTS = 16
UNKNOWN_PERSON, NOT_OWNER, ADULTS_ONLY, NOT_IN_MANIFEST = "unknown_person", "not_owner", "adults_only", "not_in_manifest"
ERASED = "erased"                       # the ask or report was made before a forget of a member it names: nothing is recorded
MAX_STAMPS = 4
_ID = re.compile(r"[A-Za-z0-9._:@_-]{1,80}\Z")   # the id shapes Zoe uses: uuids, and ids such as ``member_ova`` / ``demo_bar_0001``


@dataclass(frozen=True)
class Area:
    """One area of the door. ``decide(people, verb, actor, *, via, owner, visibility) -> Decision`` is its whole permission table;
    ``target`` is the record's key for the id of the thing changed, ``parts`` the key for the ids of its parts (None: it has none)."""
    name: str
    verbs: tuple
    decide: Callable
    target: str
    parts: str | None = None


def _id(value, *, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError("not an id")
    return value


def _stamps(value) -> dict:
    """``{member id: erase stamp}`` as the caller sent it, checked: ids and stamp pairs only."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_STAMPS:
        raise ValueError("malformed stamps")
    return {_id(member, optional=False): erase_stamp(stamp) for member, stamp in value.items()}


class Door:
    """``ask`` then ``report``, for every area. Both run under the spine lock (the server takes it; direct callers get it here)."""

    def __init__(self, spine, enforcer, areas) -> None:
        self.spine, self.enforcer = spine, enforcer
        self.areas = {area.name: area for area in areas}
        self.verbs = {verb: area for area in areas for verb in area.verbs}
        if len(self.verbs) != sum(len(area.verbs) for area in areas):
            raise ValueError("a verb belongs to one area")       # a report finds its ask's area by the verb

    def ask(self, skill: str, area: str, verb: str, person: str, *, via: str | None = None, target: str | None = None, parts=(),
            owner: str | None = None, visibility: str | None = None, turn: str | None = None, stamps=None) -> dict:
        """Allowed: ``{"ask": intent id, "seq": the record}``. Refused: raises ``Refused`` whose wire text is the reason code.
        ``stamps``: the caller's erase stamp per member named (module docstring, "ORDER AGAINST A FORGET")."""
        spec = self.areas.get(area) if isinstance(area, str) else None
        if spec is None or verb not in spec.verbs or via not in (None, PANEL) or visibility not in (None, HOUSEHOLD, PRIVATE):
            raise ValueError("unknown area, verb, surface or visibility")
        if not isinstance(person, str) or not isinstance(parts, (list, tuple)) or len(parts) > MAX_PARTS \
                or (parts and spec.parts is None):
            raise ValueError("malformed ask")
        target, owner, turn = _id(target), _id(owner), _id(turn)
        parts = [_id(i, optional=False) for i in parts]
        visibility = visibility or PRIVATE
        stamps = _stamps(stamps)
        with self.spine._lock:
            people = self.enforcer.store.people
            # made before a forget of the acting person or the owner: decided, but nothing that names anyone is written
            stale = any(self.spine.erased_after(m, stamps.get(m)) for m in (None if via == PANEL else person, owner) if m)
            known = via != PANEL and person in people and not stale
            manifest = self.enforcer.store.manifests.get(skill)
            verdict = Decision(False, NOT_IN_MANIFEST) if manifest is None or verb not in manifest.acts else \
                spec.decide(people, verb, person, via=via, owner=owner, visibility=visibility)
            if verdict.allowed and owner is not None and owner not in people:
                # an owner the kernel does not know is not a person it could later FORGET (and no erase mark could order a late
                # ask against them): their id is never written. Not on the roster = refused, for an owner as for an actor.
                verdict = Decision(False, UNKNOWN_PERSON)
            if not verdict.allowed:
                self._refuse(skill, verb, verdict.reason, person if known else None, spec.target, target)
            if stale:
                self._refuse(skill, verb, ERASED, None, spec.target, None)
            grant = self.enforcer.store.of(skill, person) if known else None
            payload = {"action": verb, "skill": skill, "grant_id": grant.id if grant else None, "proof": None, "verified": False,
                       "outcome": "allowed", "reversible": False, "vis": visibility}
            for key, value in ((spec.target, target), (spec.parts, parts), ("owner", owner), ("turn", turn), ("via", via)):
                if key and value:
                    payload[key] = value
            opened: list = []

            def open_ask(seq: int) -> None:      # INSIDE the receipt's transaction: an allowed receipt never exists without its
                opened.append(self.spine._db.execute(    # open ask (a crash between the two would leave one recovery cannot close)
                    "INSERT INTO intents (ts, skill, action, person, grant_id, accept_seq, caused_by, undoes) VALUES (?,?,?,?,?,?,?,?)",
                    (self.spine._clock(), skill, verb, person if known else PANEL, payload["grant_id"], None,
                     json.dumps([seq]), None)).lastrowid)

            record = self.spine._kernel_append(self._event(person if known else None, payload), open_ask)
            return {"ask": opened[0], "seq": record.seq}

    def report(self, skill: str, ask: int, outcome: str, *, stamps=None) -> dict:
        """Close one open ask of this skill's with what production says happened. Told, never verified: ``verified`` stays False.
        ``stamps``: the stamps the ask carried; a stale one refuses the report (``erased``) before anything is read or written."""
        if outcome not in OUTCOMES or type(ask) is not int:
            raise ValueError("malformed report")
        stamps = _stamps(stamps)
        with self.spine._lock:
            if any(self.spine.erased_after(member, stamp) for member, stamp in stamps.items()):
                self._refuse(skill, "report", ERASED, None, "", None)
            row = self.spine._db.execute(
                "SELECT action, person, caused_by FROM intents WHERE id = ? AND skill = ? AND receipt_seq IS NULL",
                (ask, skill)).fetchone()
            if row is None or row[0] not in self.verbs:
                raise ValueError("no such open ask")
            verb, person, causes = row[0], row[1], tuple(c for c in json.loads(row[2] or "[]") if self.spine._get(c))
            payload = {"action": verb, "skill": skill, "grant_id": None, "proof": None, "verified": False, "outcome": outcome,
                       "reversible": False, "told_by": skill}
            ev = self.spine._kernel_append(
                self._event(person if person in self.enforcer.store.people else None, payload, causes),
                lambda seq: self.spine._db.execute("UPDATE intents SET receipt_seq = ? WHERE id = ?", (seq, ask)))
            return {"seq": ev.seq, "outcome": outcome}

    @staticmethod
    def _event(person: str | None, payload: dict, causes: tuple = ()) -> Event:
        """A known person's record is theirs; the panel's (nobody identified) is the household's."""
        return Event(Kind.ACTION_RECEIPT, KERNEL, subject=person, caused_by=causes, payload=payload,
                     visibility=Visibility.PRIVATE if person else Visibility.HOUSEHOLD)

    def _refuse(self, skill: str, verb: str, reason: str, person: str | None, key: str, target: str | None) -> None:
        """A refusal event with fixed codes. An unknown id is never recorded (it is caller text, not a person)."""
        self.spine.refusals += 1
        payload = {"who": skill, "attempted": f"act {verb}", "reason": reason}
        if person and target:
            payload[key] = target
        record = Event(Kind.REFUSAL, KERNEL, subject=person, visibility=Visibility.KERNEL, payload=payload)
        if self.spine._payload_refusal(record):
            record = Event(Kind.REFUSAL, KERNEL, visibility=Visibility.KERNEL)
        self.spine._append([record])
        raise Refused(reason, reason=reason, wire=reason)
