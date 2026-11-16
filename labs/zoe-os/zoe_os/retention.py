"""Retention: the event log is itself sensitive, so every kind of event has an age limit.

An event passes through three stages. A ``Rule`` gives the two ages, in days, per kind:

1. FULL, for ``full_days``: everything, as published.
2. SKELETON, for ``skeleton_days`` more: the content and the subject are erased from the row
   and from the disk. What stays is the sequence number, the time, the kind, and the links to
   whom it concerned and what derived from it.
3. COUNT ONLY, after that: the row is deleted. One number per day per kind remains, about
   no one.

How FORGET interacts, which is why stage 2 exists:
* a forget must still reach everything built on an event after its content has aged out, so
  the skeleton keeps the who and the derived-from links, and a skeleton is NOT retired to a
  count while anything derived from it is still on the log;
* a forget of a skeleton removes those links too and marks the row forgotten; a forgotten row
  is later deleted WITHOUT being counted;
* expiry is not a forget: nothing goes into the shield, the words may be said again;
* the forget notice itself must never expire (a subscriber that was offline has to be told),
  and the keyed hashes of forgotten words and names are not events and are kept.

THE POLICY IS THE OWNER'S DECISION (2026-11-14, ``docs/architecture/zoe-os.md`` section 9):
option B, ``balanced``. ``POLICY`` is that choice and the kernel holds it as ``kernel.retention``.
The other two ``OPTIONS`` stay as the record of what was not chosen. ``TWIN_WINDOW_DAYS`` is
the same decision's line for the household twin's reduced facts (section 11).

NOTHING RUNS BY ITSELF. There is no scheduler: events age only when ``sweep`` is called.
``Retention`` still refuses an incomplete policy.
"""
from __future__ import annotations

from dataclasses import dataclass

from .events import Kind
from .spine import Spine, _chunks

DAY = 86400.0
KEEP = None                     # no limit


@dataclass(frozen=True)
class Rule:
    full_days: float | None                 # None: never erased by age
    skeleton_days: float | None = KEEP      # further days as a skeleton; None: skeleton kept


Policy = dict[Kind, Rule]


def _policy(words, where, did, proposed, proof) -> Policy:
    """words: what was said. where: presence and sensors. did: routines, calendar, timers,
    reminders, turns. proposed: offers, answers, how they were routed, claims. proof:
    receipts, refusals, findings. Each a (full, skeleton) pair in days."""
    groups = (
        (words, (Kind.UTTERANCE,)),
        (where, (Kind.PRESENCE, Kind.SENSOR)),
        (did, (Kind.ROUTINE_STEP, Kind.CALENDAR_CHANGE, Kind.TIMER_FIRED, Kind.REMINDER_FIRED, Kind.TURN_END)),
        (proposed, (Kind.OFFER, Kind.OFFER_RESPONSE, Kind.ATTENTION, Kind.CLAIM)),
        (proof, (Kind.ACTION_RECEIPT, Kind.REFUSAL, Kind.AUDIT_FINDING)),
    )
    policy = {kind: Rule(*pair) for pair, kinds in groups for kind in kinds}
    return policy | {Kind.FORGET: Rule(KEEP), Kind.GRANT_CHANGE: Rule(KEEP)}


OPTIONS: dict[str, Policy] = {
    #                  words      where      did        proposed   proof
    "short": _policy((7, 23), (3, 27), (30, 60), (30, 60), (90, 275)),
    "balanced": _policy((30, 335), (14, 351), (90, 275), (90, 275), (365, 365)),
    "long": _policy((365, 365), (90, 640), (365, 365), (365, 365), (730, 730)),
}


CHOSEN = "balanced"                     # the owner's decision; change it there first, then here
POLICY: Policy = OPTIONS[CHOSEN]
TWIN_WINDOW_DAYS = 28                   # how long the twin keeps a person's reduced days


class Retention:
    def __init__(self, spine: Spine, policy: Policy) -> None:
        missing = sorted(k.value for k in Kind if k not in policy)
        if missing:
            raise ValueError(f"a retention policy needs a rule for every kind; missing {missing}")
        if policy[Kind.FORGET].full_days is not KEEP:
            raise ValueError("a forget notice must never expire: an offline subscriber has to be told")
        claim, proof = policy[Kind.CLAIM].full_days, policy[Kind.ACTION_RECEIPT].full_days
        if proof is not KEEP and (claim is KEEP or claim > proof):
            raise ValueError("a receipt must be kept at least as long as the claims it backs")
        self.spine, self.policy = spine, policy

    def sweep(self, now: float | None = None) -> dict[str, int]:
        """Move every event that is past its age to the next stage. Idempotent."""
        now = self.spine._clock() if now is None else now
        db, expire, retire = self.spine._db, [], []
        limited = [(k, r) for k, r in self.policy.items() if r.full_days is not KEEP]
        with self.spine._lock:
            for kind, rule in limited:
                expire += [r[0] for r in db.execute(
                    "SELECT seq FROM events WHERE kind = ? AND tombstoned = 0 AND ts < ?",
                    (kind.value, now - rule.full_days * DAY))]
            self.spine._expire(sorted(expire))
            for kind, rule in limited:              # after expiring: one sweep can do both steps
                if rule.skeleton_days is not KEEP:
                    retire += [r[0] for r in db.execute(
                        "SELECT seq FROM events WHERE kind = ? AND tombstoned != 0 AND ts < ?",
                        (kind.value, now - (rule.full_days + rule.skeleton_days) * DAY))]
            retire = sorted(self._childless(retire))
            self.spine._retire(retire)
        return {"expired": len(expire), "retired": len(retire)}

    def _childless(self, seqs: list[int]) -> set[int]:
        """A skeleton stays while anything derived from it is still on the log: it is the link
        a forget follows. (Remove this and a forget silently misses those derived events.)"""
        held: set[int] = set()
        for chunk in _chunks(seqs):
            marks = ",".join("?" * len(chunk))
            held |= {r[0] for r in self.spine._db.execute(
                f"SELECT DISTINCT cause FROM causes WHERE cause IN ({marks})", chunk)}
        return set(seqs) - held

    def counts(self) -> list[tuple[str, str, int]]:
        """Stage 3: (day, kind, how many). No person, no source, no content."""
        return self.spine._db.execute("SELECT day, kind, n FROM retired_counts ORDER BY day, kind").fetchall()

    def stage(self, seq: int) -> str:
        row = self.spine._db.execute("SELECT tombstoned FROM events WHERE seq = ?", (seq,)).fetchone()
        return "gone" if row is None else ("full", "forgotten", "skeleton")[row[0]]
