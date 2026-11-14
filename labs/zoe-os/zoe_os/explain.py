"""Explain: "why did you do that?" answered from the log, for a PERSON.

``Explainer.why(seq, person=...)`` returns, for any action receipt, refusal, offer (or any
other event that person may see): the event, the chain of events it derives from (every
ancestor, oldest first), the grant it ran under, what happened next (how an offer was routed,
answered and acted on), and ONE plain sentence built by code from kinds, names and ids. No
model writes it, and it never quotes free text.

The privacy rule is the point. A chain can cross people: a skill two people enabled may
derive something for one of them from the other's private words. The asker sees a node only
if ``may_see`` says so; every other node is reduced to a COUNT (``hidden``). Not its kind,
not its subject, not its sequence number. An event the asker may not see, and an event that
does not exist, are refused with the same words, so asking is not a way to probe.

* private: the subject themself, or a guardian of a child subject;
* household: any member of the household who is not a guest;
* kernel (refusals, grant changes, findings): the person it concerns, else adults only.
"""
from __future__ import annotations

from .capability import ADULT, GUEST, Person
from .events import Event, Kind, Visibility
from .spine import Spine

MAX_CHAIN = 64
LAST = {"action": Kind.ACTION_RECEIPT, "offer": Kind.OFFER, "refusal": Kind.REFUSAL}
_NO = "not an event you may ask about"


class Explainer:
    def __init__(self, spine: Spine, people: dict[str, Person]) -> None:
        self.spine, self.people = spine, people

    # ---- who may see what ---------------------------------------------------------------
    def _is(self, asker: str, person: str | None) -> bool:
        owner = self.people.get(person or "")
        return bool(owner) and (asker == owner.id or asker in owner.guardians)

    def _may_see(self, asker: str, ev: Event) -> bool:
        who = self.people.get(asker)
        if who is None:
            return False
        if ev.visibility is Visibility.HOUSEHOLD:
            return who.role != GUEST
        concerns = ev.subject or ev.payload.get("person")
        if concerns or ev.visibility is Visibility.PRIVATE:
            return self._is(asker, concerns)
        return who.role == ADULT                   # a kernel event about nobody in particular

    # ---- the query --------------------------------------------------------------------------
    def last(self, person: str, what: str = "action") -> int | None:
        """The newest action receipt / offer about this person ("that" in "why did you do that")."""
        rows = self.spine._fetch(0, frozenset({LAST[what]}), person if what != "refusal" else None,
                                 1_000_000)
        mine = [e.seq for e in rows if e.kind is LAST[what] and self._may_see(person, e)]
        return mine[-1] if mine else None

    def why(self, seq: int, *, person: str) -> dict:
        with self.spine._lock:
            ev = self.spine._get(seq)
            if ev is None or not self._may_see(person, ev):
                self.spine._refuse(person, "explain", _NO)
            chain, hidden = self._ancestors(ev, person)
            then = [c for c in self._children(ev.seq) if self._may_see(person, c)]
            grant = self._grant(ev.payload.get("grant_id"), person)
            shown = {e.seq for e in (ev, *chain, *then)}       # a hidden node's number is hidden too
            return {"seq": ev.seq, "kind": ev.kind.value, "what": _node(ev, shown),
                    "chain": [_node(c, shown) for c in chain], "grant": grant,
                    "then": [_node(c, shown) for c in then], "hidden": hidden,
                    "sentence": _sentence(ev, chain, grant, then, hidden)}

    def _ancestors(self, ev: Event, asker: str) -> tuple[list[Event], int]:
        """Every event this one derives from, to any depth: (the visible ones oldest first, how
        many the asker may not see). A hidden node's own causes are not followed: what led to
        someone else's private event is theirs too."""
        seen, visible, hidden = {ev.seq}, [], 0
        frontier = list(ev.caused_by)
        while frontier and len(seen) < MAX_CHAIN:
            seq = frontier.pop()
            if seq in seen:
                continue
            seen.add(seq)
            cause = self.spine._get(seq)
            if cause is None or not self._may_see(asker, cause):
                hidden += 1
            else:
                visible.append(cause)
                frontier += cause.caused_by
        return sorted(visible, key=lambda e: e.seq), hidden

    def _children(self, seq: int) -> list[Event]:
        rows = self.spine._db.execute("SELECT seq FROM causes WHERE cause = ? ORDER BY seq", (seq,))
        return [e for (s,) in rows.fetchall() if (e := self.spine._get(s)) is not None]

    def _grant(self, grant_id, asker: str) -> dict | None:
        if not grant_id:
            return None
        for e in reversed(self.spine._fetch(0, frozenset({Kind.GRANT_CHANGE}), None, 1_000_000)):
            if e.payload.get("grant_id") == grant_id and e.payload.get("change") == "granted":
                return {"seq": e.seq, **e.payload} if self._may_see(asker, e) else None
        return None


def _node(ev: Event, shown: set[int]) -> dict:
    return {"seq": ev.seq, "kind": ev.kind.value, "source": ev.source, "subject": ev.subject,
            "visibility": ev.visibility.value, "payload": ev.payload,
            "caused_by": [c for c in ev.caused_by if c in shown]}


def _because(chain: list[Event], hidden: int) -> str:
    parts = [f"{c.kind.value.replace('_', ' ')} #{c.seq}" for c in chain]
    if hidden:
        parts.append(f"{hidden} event{'s' if hidden > 1 else ''} you may not see")
    return " It followed from: " + ", ".join(parts) + "." if parts else " Nothing led to it."


_HOW = {"verified": "and the read-back confirmed it", "uncertain": "but the outcome is uncertain",
        "unverified": "but the read-back did not confirm it"}


def _sentence(ev: Event, chain, grant, then, hidden: int) -> str:
    """One plain sentence (then the chain). Kinds, names and ids only: never free text."""
    p = ev.payload
    if ev.kind is Kind.ACTION_RECEIPT:
        how = _HOW[p.get("outcome") or ("verified" if p.get("verified") else "unverified")]
        yes = next((c for c in chain if c.kind is Kind.OFFER_RESPONSE), None)
        why = f" after {yes.subject} said yes (#{yes.seq})" if yes else " with no yes needed for this action"
        under = f", under the grant {grant['person']} gave it (#{grant['seq']})" if grant else ""
        head = f"{p.get('skill')} did {p.get('action')} for {ev.subject}{why}{under}, {how}."
    elif ev.kind is Kind.REFUSAL:
        head = f"{p.get('who')} was refused when it tried to {p.get('attempted')}: {p.get('reason')}."
    elif ev.kind is Kind.OFFER:
        route = next((c for c in then if c.kind is Kind.ATTENTION), None)
        answer = next((c for c in then if c.kind is Kind.OFFER_RESPONSE), None)
        head = f"{ev.source} made this offer to {ev.subject or 'the household'}."
        if route:
            head += f" It was routed as {route.payload['channel']} because {route.payload['reason']}."
        head += f" The answer was: {answer.payload['response']}." if answer else " It has not been answered."
    else:
        head = f"{ev.source} recorded this {ev.kind.value.replace('_', ' ')}."
    return head + _because(chain, hidden)
