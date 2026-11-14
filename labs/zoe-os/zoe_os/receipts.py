"""Intent -> act with RECEIPTS: nothing is done without a grant, and nothing is claimed done
without proof.

An action goes through ``Dispatcher.act``. The dispatcher asks the enforcer, checks the cause
(for an action marked ``needs_accept``: the person's own "yes" to an offer THIS skill made,
used once), runs the driver, then asks the driver to READ BACK what it did. The receipt
records what was done, by which skill, under which grant, with what proof, and is appended to
the spine as a kernel event. A receipt whose read-back failed is ``verified: false``: it
proves an attempt, not a result.

``ClaimAuditor`` is the "claim without receipt" detector: every CLAIM event must cite a
verified receipt for the same action and person, made by the claimant.

DURABLE across a kernel restart or a kill:
* a receipt is an event, so it was always on disk. What was NOT: which yes had been used, and
  how far the auditor had read. Both now live in the database;
* before the driver is called the dispatcher writes an INTENT row (write-ahead). The receipt
  closes it in the same transaction that stores the receipt. An intent still open when the
  kernel starts means the kernel stopped somewhere between "write sent" and "receipt stored":
  ``recover`` gives it a receipt with outcome ``uncertain``. Never ``verified``, and the
  action is NEVER run again: the kernel does not know whether the write landed, and a second
  write is not the kernel's to decide. The person's yes stays spent.

UNDO. An action registered with a ``snapshot`` and an ``undo`` is reversible: its receipt
records the arguments and the state read BEFORE the action, which is enough to put it back.
``Dispatcher.undo`` reverses the person's LAST action, for that person (or a child's
guardian) only, once only, and leaves its own receipt. "That" is always the last action: if
it cannot be reversed the answer says so, and nothing older is reversed in its place. An undo
whose read-back does not show the old state gets an unverified receipt, like any action.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from .events import Event, Kind, Visibility
from .spine import KERNEL, Spine

Do = Callable[[str, dict], Any]                    # (person, args) -> result
Verify = Callable[[str, dict, Any], "dict | None"]   # independent read-back, None = no proof
Snapshot = Callable[[str, dict], "dict | None"]    # (person, args) -> the state this would change
Undo = Callable[[str, dict, dict], Any]            # (person, args, before) -> put ``before`` back
UNDO = "undo"


@dataclass(frozen=True)
class Receipt:
    id: str
    seq: int
    action: str
    skill: str
    on_behalf_of: str
    grant_id: str
    proof: dict | None
    verified: bool
    outcome: str = "verified"     # verified | unverified | uncertain (see ``Dispatcher.recover``)
    reversible: bool = False      # the receipt holds what ``Dispatcher.undo`` needs


class Dispatcher:
    def __init__(self, spine: Spine, enforcer) -> None:
        self.spine, self.enforcer = spine, enforcer
        self._actions: dict[str, tuple[Do, Verify, bool]] = {}
        self._reverse: dict[str, tuple[Snapshot, Undo]] = {}

    def register(self, action: str, do: Do, verify: Verify, *, needs_accept: bool = True,
                 snapshot: Snapshot | None = None, undo: Undo | None = None) -> None:
        """``snapshot`` and ``undo`` together make the action reversible. Without both it is
        not, its receipts say so, and ``undo`` will refuse it rather than pretend."""
        self._actions[action] = (do, verify, needs_accept)
        if snapshot is not None and undo is not None:
            self._reverse[action] = (snapshot, undo)

    def act(self, skill: str, action: str, on_behalf_of: str, args: dict, *,
            caused_by: tuple[int, ...]) -> Receipt:
        """Run one action for one person. Raises ``Refused`` (and records the refusal)."""
        attempted = f"act {action}"
        with self.spine._lock:
            verdict = self._authorize(skill, action, on_behalf_of)
            if not verdict.allowed:
                self.spine._refuse(skill, attempted, verdict.reason)
            if action not in self._actions:
                self.spine._refuse(skill, attempted, "no such action")
            do, verify, needs_accept = self._actions[action]
            reason = self.spine._cause_refusal(skill, caused_by) if caused_by else \
                "an action must cite the event that asked for it"
            accept = None
            if not reason and needs_accept:
                accept, reason = self._find_accept(skill, on_behalf_of, caused_by)
            if reason:
                self.spine._refuse(skill, attempted, reason)
            intent = self._open_intent(skill, action, on_behalf_of, verdict.grant_id, accept, caused_by)
            proof, error, before = None, "", None
            try:
                if action in self._reverse:
                    before = self._reverse[action][0](on_behalf_of, args)
                result = do(on_behalf_of, args)
                proof = verify(on_behalf_of, args, result)
            except Exception as exc:   # a failed action still leaves a receipt, unverified
                error = type(exc).__name__
            digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]
            outcome = "verified" if proof else "unverified"
            payload = {"action": action, "skill": skill, "grant_id": verdict.grant_id,
                       "args_digest": digest, "proof": proof, "verified": bool(proof),
                       "outcome": outcome, "error": error, "reversible": before is not None}
            if before is not None:      # enough to reverse it: what was asked, and what was there
                payload["undo"] = {"args": args, "before": before}
            ev = self._close_intent(intent, Event(
                Kind.ACTION_RECEIPT, KERNEL, subject=on_behalf_of, caused_by=caused_by, payload=payload))
            return Receipt(f"r{ev.seq}", ev.seq, action, skill, on_behalf_of,
                           verdict.grant_id or "", proof, bool(proof), outcome, before is not None)

    # ---- undo ---------------------------------------------------------------------------------
    def _may_undo(self, actor: str, person: str) -> bool:
        """The person whose action it was, or a guardian of that child. Nobody else, no skill."""
        return self.enforcer.may_forget(actor, person).allowed

    def _undone(self, seq: int) -> bool:
        return self.spine._db.execute("SELECT 1 FROM intents WHERE undoes = ?", (seq,)).fetchone() is not None

    def last_action(self, person: str) -> Event | None:
        rows = self.spine._fetch(0, frozenset({Kind.ACTION_RECEIPT}), person, 1_000_000)
        mine = [e for e in rows if e.kind is Kind.ACTION_RECEIPT and e.subject == person]
        return mine[-1] if mine else None

    def undo(self, person: str, *, actor: str) -> Receipt:
        """Reverse ``person``'s last action. Raises ``Refused`` (recorded) when it may not or cannot."""
        with self.spine._lock:
            if not self._may_undo(actor, person):
                self.spine._refuse(actor, UNDO, "not theirs to undo")
            last = self.last_action(person)
            why = "there is nothing to undo" if last is None else self._cannot_undo(last)
            if why:
                self.spine._refuse(actor, UNDO, why)
            action, saved = last.payload["action"], last.payload["undo"]
            snapshot, reverse = self._reverse[action]
            intent = self._open_intent(KERNEL, UNDO, person, None, None, (last.seq,), undoes=last.seq)
            proof, error = None, ""
            try:
                reverse(person, saved["args"], saved["before"])
                if snapshot(person, saved["args"]) == saved["before"]:      # the read-back
                    proof = {"restored": saved["before"]}
            except Exception as exc:
                error = type(exc).__name__
            outcome = "verified" if proof else "unverified"
            ev = self._close_intent(intent, Event(
                Kind.ACTION_RECEIPT, KERNEL, subject=person, caused_by=(last.seq,),
                payload={"action": UNDO, "of": action, "undoes": last.seq, "skill": KERNEL,
                         "asked_by": actor, "grant_id": None, "proof": proof, "verified": bool(proof),
                         "outcome": outcome, "error": error, "reversible": False}))
            return Receipt(f"r{ev.seq}", ev.seq, UNDO, KERNEL, person, "", proof, bool(proof), outcome)

    def _cannot_undo(self, last: Event) -> str:
        p = last.payload
        if p.get("action") == UNDO:
            return "the last action was itself an undo"
        if self._undone(last.seq):
            return "that was already undone"
        if p.get("outcome", "verified") != "verified":
            return "that action was never confirmed done"
        if not p.get("reversible") or p.get("action") not in self._reverse:
            return "that action cannot be undone"
        return ""

    # ---- write-ahead intent: what makes a crash between the write and the receipt visible ------
    def _open_intent(self, skill, action, person, grant_id, accept, caused_by, undoes=None) -> int:
        """Durable BEFORE the driver runs. It also spends the yes (or the one undo), for good."""
        return self.spine._db.execute(
            "INSERT INTO intents (ts, skill, action, person, grant_id, accept_seq, caused_by, undoes)"
            " VALUES (?,?,?,?,?,?,?,?)", (self.spine._clock(), skill, action, person, grant_id, accept,
                                          json.dumps(list(caused_by)), undoes)).lastrowid

    def _close_intent(self, intent: int, receipt: Event) -> Event:
        """The receipt and the closing of its intent are one transaction."""
        return self.spine._kernel_append(receipt, lambda seq: self.spine._db.execute(
            "UPDATE intents SET receipt_seq = ? WHERE id = ?", (seq, intent)))

    def _accept_used(self, seq: int) -> bool:
        return self.spine._db.execute(
            "SELECT 1 FROM intents WHERE accept_seq = ?", (seq,)).fetchone() is not None

    def _uncertain(self, action: str, skill: str, grant_id: str | None) -> dict:
        """What the kernel can honestly say about an action it did not see finish."""
        return {"action": action, "skill": skill, "grant_id": grant_id, "proof": None,
                "verified": False, "outcome": "uncertain",
                "error": "the kernel stopped between sending the write and storing the receipt"}

    def recover(self) -> list[Event]:
        """Called once when the kernel starts. Every intent left open gets an ``uncertain``
        receipt and an audit finding. Nothing is re-run and no driver is called."""
        out: list[Event] = []
        with self.spine._lock:
            rows = self.spine._db.execute(
                "SELECT id, skill, action, person, grant_id, caused_by, undoes FROM intents"
                " WHERE receipt_seq IS NULL ORDER BY id").fetchall()
            for intent, skill, action, person, grant_id, caused_by, undoes in rows:
                causes = tuple(c for c in json.loads(caused_by) if self.spine._get(c))
                payload = self._uncertain(action, skill, grant_id) | {"reversible": False}
                if undoes is not None:          # an undo in flight: just as uncertain, and spent
                    payload["undoes"] = undoes
                ev = self._close_intent(intent, Event(
                    Kind.ACTION_RECEIPT, KERNEL, subject=person, caused_by=causes, payload=payload))
                self.spine._kernel_append(Event(
                    Kind.AUDIT_FINDING, KERNEL, visibility=Visibility.KERNEL,
                    payload={"finding": "action_outcome_uncertain", "who": skill, "action": action,
                             "receipt_seq": ev.seq}))
                out.append(ev)
        return out

    def _authorize(self, skill: str, action: str, on_behalf_of: str):
        """The one capability check on the action path."""
        return self.enforcer.may_act(skill, action, on_behalf_of)

    def _find_accept(self, skill: str, person: str, caused_by) -> tuple[int | None, str]:
        """The cause must hold this person's unused yes to an offer this skill made."""
        for seq in caused_by:
            ev = self.spine._get(seq)
            if ev is None or ev.kind is not Kind.OFFER_RESPONSE:
                continue
            if ev.subject != person or ev.payload.get("response") != "accepted":
                continue
            if not self.enforcer.is_origin(ev.source):
                continue                       # only a trusted surface can record a person's yes
            offers = [self.spine._get(c) for c in ev.caused_by]
            if not any(o and o.kind is Kind.OFFER and o.source == skill
                       and o.payload.get("offer_id") == ev.payload.get("offer_id") for o in offers):
                continue
            if self._accept_used(seq):
                return None, "that yes was already used"
            return seq, ""
        return None, "the person has not accepted an offer for this"


class ClaimAuditor:
    """Finds CLAIM events that no verified receipt backs, and records each as a finding."""

    READER = "kernel.claim-auditor"     # its cursor is durable: a restart neither skips nor repeats

    def __init__(self, spine: Spine, enforcer) -> None:
        self.spine, self.enforcer = spine, enforcer
        row = spine._db.execute("SELECT seq FROM cursors WHERE reader = ?", (self.READER,)).fetchone()
        self._cursor = row[0] if row else 0

    def _save(self, seq: int) -> None:
        self._cursor = seq
        self.spine._db.execute(
            "INSERT INTO cursors VALUES (?, ?) ON CONFLICT(reader) DO UPDATE SET seq = excluded.seq",
            (self.READER, seq))

    def backed(self, claim: Event) -> bool:
        seq = claim.payload.get("receipt_seq")
        receipt = self.spine._get(seq) if isinstance(seq, int) else None
        return bool(
            receipt is not None and receipt.kind is Kind.ACTION_RECEIPT and receipt.seq < claim.seq
            and receipt.payload.get("verified") is True
            and receipt.payload.get("action") == claim.payload.get("action")
            and receipt.subject == claim.subject
            and receipt.payload.get("skill") == claim.source)

    def scan(self) -> list[Event]:
        """Check every claim since the last scan. Returns the unbacked ones."""
        unbacked: list[Event] = []
        with self.spine._lock:
            while True:
                rows = self.spine._fetch(self._cursor, frozenset({Kind.CLAIM}), None, 500)
                for claim in rows:
                    if self.backed(claim):
                        continue
                    unbacked.append(claim)
                    self.spine._kernel_append(Event(        # the finding and the cursor: one commit
                        Kind.AUDIT_FINDING, KERNEL, visibility=Visibility.KERNEL,
                        payload={"finding": "claim_without_receipt", "who": claim.source,
                                 "action": claim.payload.get("action"), "claim_seq": claim.seq}),
                        lambda _seq, at=claim.seq: self._save(at))
                if not rows:
                    return unbacked
                self._save(rows[-1].seq)
