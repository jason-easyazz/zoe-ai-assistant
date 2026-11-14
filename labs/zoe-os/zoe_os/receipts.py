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


class Dispatcher:
    def __init__(self, spine: Spine, enforcer) -> None:
        self.spine, self.enforcer = spine, enforcer
        self._actions: dict[str, tuple[Do, Verify, bool]] = {}
        self._used_accepts: set[int] = set()

    def register(self, action: str, do: Do, verify: Verify, *, needs_accept: bool = True) -> None:
        self._actions[action] = (do, verify, needs_accept)

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
            if accept is not None:
                self._used_accepts.add(accept)
            proof, error = None, ""
            try:
                result = do(on_behalf_of, args)
                proof = verify(on_behalf_of, args, result)
            except Exception as exc:   # a failed action still leaves a receipt, unverified
                error = type(exc).__name__
            digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]
            ev = self.spine._kernel_append(Event(
                Kind.ACTION_RECEIPT, KERNEL, subject=on_behalf_of, caused_by=caused_by,
                payload={"action": action, "skill": skill, "grant_id": verdict.grant_id,
                         "args_digest": digest, "proof": proof, "verified": bool(proof),
                         "error": error}))
            return Receipt(f"r{ev.seq}", ev.seq, action, skill, on_behalf_of,
                           verdict.grant_id or "", proof, bool(proof))

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
            if seq in self._used_accepts:
                return None, "that yes was already used"
            return seq, ""
        return None, "the person has not accepted an offer for this"


class ClaimAuditor:
    """Finds CLAIM events that no verified receipt backs, and records each as a finding."""

    def __init__(self, spine: Spine, enforcer) -> None:
        self.spine, self.enforcer = spine, enforcer
        self._cursor = 0

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
        while True:
            rows = self.spine._fetch(self._cursor, frozenset({Kind.CLAIM}), None, 500)
            for claim in rows:
                if not self.backed(claim):
                    unbacked.append(claim)
                    self.spine._kernel_append(Event(
                        Kind.AUDIT_FINDING, KERNEL, visibility=Visibility.KERNEL,
                        payload={"finding": "claim_without_receipt", "who": claim.source,
                                 "action": claim.payload.get("action"), "claim_seq": claim.seq}))
            if not rows:
                return unbacked
            self._cursor = rows[-1].seq
