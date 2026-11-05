"""provenance_speech - "you told me on Tuesday": a memory's provenance as words Zoe can say, built from POINTERS only.

Why (Samantha initiative item 15, lane C E8; ``docs/research/samantha-initiative-2026-11-03/lane-C-memory.md`` section 3.4): the
recall packet dates a row ("Mon 22 Sep, 8 days ago", ``recall_evidence``) but does not say WHO it came from, so the brain can say
"you told me" about something Zoe only inferred. "How do you know that?" after a reply is answered by ``provenance_answers``; this is
the same honesty for the memory lines themselves, and for the questions Zoe asks about them (``memory_slot_check``).

What. ``of(meta)`` reads three things the row already carries and copies nothing: the authority class the write was stamped with
(``memory_authority``), the capture time (``recall_evidence.row_epoch``) and the writer (``recall_evidence.effective_writer``). It
returns a ``Provenance`` (a verb and a spoken day) or ``None``:

* ``user_confirmed`` -> "you confirmed it" ("you confirmed it from your day review" for an approved pendant candidate);
* ``user_stated`` (and a model write the owner's own turn entailed verbatim) -> "you told me";
* ``user_stated_derived`` -> "you mentioned" (a paraphrase the owner's turn supports, not their words);
* ``model_from_turn`` / ``model_from_transcript`` -> "I picked up" (an inference: never "you told me");
* the day: "earlier today" | "yesterday" | "on Tuesday" | "on 3 October" (``provenance_answers.spoken_day``), and NO day for a batch
  writer (the night digest stores at 03:00 over a 30 h lookback: its capture time is not when anyone said anything).

It FAILS CLOSED (``None``, so nothing is said): a row with no stamped provenance (a legacy row whose class is only derived on read - a
guess is not a pointer), a ``user_unverified`` voice (never "you" for a speaker the gate did not confirm), an operator row, and an
unknown class.

Flag ``ZOE_SPEAKABLE_PROVENANCE`` (default OFF, per-call read): when on, the recall packet's dated suffix becomes the spoken clause
(``routers/memories._build_memory_prompt_packet``, relevance mode only). Off = byte-identical. Pure; stdlib + sibling pure helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

ENV = "ZOE_SPEAKABLE_PROVENANCE"

#: the writer of an approved pendant day-review candidate (``omi_review_apply``)
_DAY_REVIEW_WRITER = "omi_review"


def enabled() -> bool:
    """Per-call env read through the canonical bool parse (default OFF)."""
    from typed_env import env_bool

    return env_bool(ENV, False)


@dataclass(frozen=True)
class Provenance:
    verb: str          # "you told me" | "you confirmed it" | "you mentioned" | "I picked up" | ...
    day: str = ""      # "earlier today" | "yesterday" | "on Tuesday" | "on 3 October" | "" (no reliable day)
    inferred: bool = False

    def clause(self) -> str:
        """"you told me on Tuesday" / "I picked up yesterday" / "you told me"."""
        return f"{self.verb} {self.day}".strip()

    def lead(self) -> str:
        """The clause turned round to open a sentence: "On Tuesday you told me" / "Yesterday I picked up" / "You told me"."""
        if not self.day:
            return self.verb[:1].upper() + self.verb[1:]
        return f"{self.day[:1].upper()}{self.day[1:]} {self.verb}"


def _stamped(meta: Mapping[str, Any]) -> bool:
    """Was this row's provenance STAMPED at write time? A class derived on read (``legacy_class``) is a rule's guess, not a pointer."""
    import memory_authority as ma

    return str(meta.get("authority_class") or "") in ma.RANK or str(meta.get("authority") or "") in ma.AUTHORITIES


def of(meta: Optional[Mapping[str, Any]], text: str = "", *, now: Optional[float] = None) -> Optional[Provenance]:
    """The speakable provenance of one stored row, or ``None`` when nothing honest can be said. Pure (``now`` injectable)."""
    import memory_authority as ma
    import recall_evidence

    meta = meta or {}
    if not _stamped(meta) or ma.is_unverified(meta):
        return None
    cls = ma.row_class(meta, text)
    writer = recall_evidence.effective_writer(meta)
    origin = str(meta.get("origin") or writer or "")
    if cls == ma.USER_CONFIRMED:
        verb = "you confirmed it from your day review" if origin == _DAY_REVIEW_WRITER else "you confirmed it"
    elif cls == ma.USER_STATED:
        verb = "you told me"
    elif cls == ma.USER_STATED_DERIVED:
        verb = "you mentioned"
    elif cls in (ma.MODEL_FROM_TURN, ma.MODEL_FROM_TRANSCRIPT):
        verb = "I picked up"
    else:            # operator / user_unverified / anything new: not something to say to the member
        return None
    day = ""
    if writer not in recall_evidence.BATCH_WRITERS:
        epoch = recall_evidence.row_epoch(meta)
        if epoch is not None:
            try:
                import provenance_answers

                day = provenance_answers.spoken_day(epoch, now)
            except (OverflowError, OSError, ValueError):
                day = ""
    return Provenance(verb=verb, day=day, inferred=cls in (ma.MODEL_FROM_TURN, ma.MODEL_FROM_TRANSCRIPT))


def packet_suffix(meta: Optional[Mapping[str, Any]], text: str = "", *, now: Optional[float] = None) -> str:
    """`` (you told me on Tuesday)`` for a recall-packet bullet; an inference says so: `` (I picked up yesterday - not confirmed)``.
    "" when nothing honest can be said."""
    p = of(meta, text, now=now)
    if p is None:
        return ""
    return f" ({p.clause()}{' - not confirmed' if p.inferred else ''})"


def that_clause(meta: Optional[Mapping[str, Any]], text: str, *, now: Optional[float] = None, lead: bool = False) -> str:
    """"you told me on Tuesday that you work at Acme" (or, ``lead``, "On Tuesday you told me that you work at Acme"): the row's own stored
    words, turned to the second person (``provenance_answers.second_person``), behind its provenance. "" when nothing honest can be said."""
    p = of(meta, text, now=now)
    if p is None or not (text or "").strip():
        return ""
    import provenance_answers

    words = provenance_answers.second_person(text)
    return f"{p.lead() if lead else p.clause()} that {words}"
