"""The lists door: the kernel DECIDES and RECORDS a household list change; the list itself stays where it is.

Owner decision 2026-11-18 (``docs/architecture/zoe-os-cutover-plan.md``): production asks before a list change ("may this person
do this verb to this list?"), makes the change itself in its own store, then reports the outcome. Nothing here holds a list.

What is recorded, and what is NOT:

* an ASK that is allowed leaves one receipt event (outcome ``allowed``: the verb, the acting person, the list id, the item ids, and
  the list's owner and visibility AS TOLD by the caller, plus the caller's turn id when given) and one write-ahead intent row;
* the REPORT closes that intent with a second small receipt (``done`` or ``failed``) that cites the first;
* an ask that is never reported is an open intent: the next start gives it an ``uncertain`` receipt (``Dispatcher.recover``), like
  every other action the kernel did not see finish;
* a REFUSAL is a refusal event (who asked, the verb, a fixed reason code; the list id only when the person is known).

IDS ONLY. No item text, no list name, no list contents, no before-state snapshot, and no row per existing item: the cost of an
act does not grow with the list (``tests/test_lists_door.py`` pins rows per act and that ``snapshot_dependencies`` stays empty).
Because no item TEXT is ever stored for these verbs, a forget has nothing of the text to scrub here; the ids, the acting person and
the told owner are ordinary event fields and member links, so a person's forget reaches these records like any other.

The rules are household rules by ROLE (``capability.Person.role``), not a consent a skill collected: a list belongs to its owner
or to the house, whatever skill carried the request. The calling skill is still bounded by its manifest (it must name the verb).
Not on the roster = unknown = refused (least access).

    list                 who                        item.add/remove/tick/edit   list.rename/delete/share   list.create
    private              its owner                  allow                       allow                      allow (their own)
    private              anyone else, the panel     refuse not_owner            refuse not_owner           refuse not_owner
    household            its owner                  allow                       allow                      allow
    household            an adult                   allow                       allow                      allow
    household            a child, a guest           allow                       refuse adults_only         allow
    household            the shared panel           allow                       refuse adults_only         allow
    any                  not on the roster          refuse unknown_person (all verbs)
"""
from __future__ import annotations

import json
import re

from .capability import ADULT
from .events import Event, Kind, Visibility
from .spine import KERNEL, Decision, Refused

ITEM_VERBS = ("item.add", "item.remove", "item.tick", "item.edit")
LIST_VERBS = ("list.rename", "list.delete", "list.share")
CREATE = "list.create"
VERBS = (CREATE, *LIST_VERBS, *ITEM_VERBS)
HOUSEHOLD, PRIVATE = "household", "private"
PANEL = "panel"                         # ``via``: the shared panel acting for the house, nobody identified
OUTCOMES = ("done", "failed")
MAX_ITEMS = 16
UNKNOWN_PERSON, NOT_OWNER, ADULTS_ONLY, NOT_IN_MANIFEST = "unknown_person", "not_owner", "adults_only", "not_in_manifest"
_ID = re.compile(r"[A-Za-z0-9._:@-]{1,80}\Z")


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster (``GrantStore.people``)."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if visibility != HOUSEHOLD:                                   # a private list: its owner only, for everything
        return Decision(True) if who is not None and owner == actor else Decision(False, NOT_OWNER)
    if verb in ITEM_VERBS or verb == CREATE:                      # a household list: any member, and the panel for the house
        return Decision(True)
    if who is not None and (owner == actor or who.role == ADULT):  # rename / delete / share: its owner or an adult
        return Decision(True)
    return Decision(False, ADULTS_ONLY)


def _id(value, *, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError("not an id")
    return value


class ListDoor:
    """``ask`` then ``report``. Both run under the spine lock (the server takes it; direct callers get it here)."""

    def __init__(self, spine, enforcer) -> None:
        self.spine, self.enforcer = spine, enforcer

    def ask(self, skill: str, verb: str, person: str, *, via: str | None = None, list_id: str | None = None, items=(),
            owner: str | None = None, visibility: str | None = None, turn: str | None = None) -> dict:
        """Allowed: ``{"ask": intent id, "seq": the record}``. Refused: raises ``Refused`` whose wire text is the reason code."""
        if verb not in VERBS or via not in (None, PANEL) or visibility not in (None, HOUSEHOLD, PRIVATE):
            raise ValueError("unknown verb, surface or visibility")
        if not isinstance(person, str) or not isinstance(items, (list, tuple)) or len(items) > MAX_ITEMS:
            raise ValueError("malformed ask")
        list_id, owner, turn = _id(list_id), _id(owner), _id(turn)
        items = [_id(i, optional=False) for i in items]
        visibility = visibility or PRIVATE
        with self.spine._lock:
            people = self.enforcer.store.people
            known = via != PANEL and person in people
            manifest = self.enforcer.store.manifests.get(skill)
            verdict = Decision(False, NOT_IN_MANIFEST) if manifest is None or verb not in manifest.acts else \
                decide(people, verb, person, via=via, owner=owner, visibility=visibility)
            if not verdict.allowed:
                self._refuse(skill, verb, verdict.reason, person if known else None, list_id)
            grant = self.enforcer.store.of(skill, person) if known else None
            payload = {"action": verb, "skill": skill, "grant_id": grant.id if grant else None, "proof": None, "verified": False,
                       "outcome": "allowed", "reversible": False, "vis": visibility}
            for key, value in (("list", list_id), ("items", items), ("owner", owner), ("turn", turn), ("via", via)):
                if value:
                    payload[key] = value
            record = self.spine._kernel_append(self._event(person if known else None, payload))
            intent = self.spine._db.execute(
                "INSERT INTO intents (ts, skill, action, person, grant_id, accept_seq, caused_by, undoes) VALUES (?,?,?,?,?,?,?,?)",
                (self.spine._clock(), skill, verb, person if known else PANEL, payload["grant_id"], None,
                 json.dumps([record.seq]), None)).lastrowid
            return {"ask": intent, "seq": record.seq}

    def report(self, skill: str, ask: int, outcome: str) -> dict:
        """Close one open ask of this skill's with what production says happened. Told, never verified: ``verified`` stays False."""
        if outcome not in OUTCOMES or type(ask) is not int:
            raise ValueError("malformed report")
        with self.spine._lock:
            row = self.spine._db.execute(
                "SELECT action, person, caused_by FROM intents WHERE id = ? AND skill = ? AND receipt_seq IS NULL",
                (ask, skill)).fetchone()
            if row is None or row[0] not in VERBS:
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

    def _refuse(self, skill: str, verb: str, reason: str, person: str | None, list_id: str | None) -> None:
        """A refusal event with fixed codes. An unknown id is never recorded (it is caller text, not a person)."""
        self.spine.refusals += 1
        payload = {"who": skill, "attempted": f"act {verb}", "reason": reason}
        if person and list_id:
            payload["list"] = list_id
        record = Event(Kind.REFUSAL, KERNEL, subject=person, visibility=Visibility.KERNEL, payload=payload)
        if self.spine._payload_refusal(record):
            record = Event(Kind.REFUSAL, KERNEL, visibility=Visibility.KERNEL)
        self.spine._append([record])
        raise Refused(reason, reason=reason, wire=reason)
