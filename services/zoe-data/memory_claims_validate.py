"""Memory claims, the DETERMINISTIC VALIDATOR (memory redesign step 4): design note rules D.1-D.20, one small function each.
Nothing in production calls this module yet.

The model proposes a claim (``memory_claims.parse_output``); this module decides, against INJECTED authority, whether it
may change anything: the member's people graph (``Graph``, read once and strictly by the caller), the member's candidate
rows with their stored attributes (``Row`` / ``Attr``, design note F), the superseded-value registry (``Registered``,
design note E) and the gate facts the caller already holds (speaker, forget, erase generation, the safety detector).
The output is a ``Verdict`` - apply | agree | abstain | hold | refuse, the deciding rule number, and the PLANNED row
edits / registry entries / event - and never text: writing a row is the single guarded writer's job (a later step).

THE HARD RULE: this module reads no phrasing. It imports no ``re``, holds no phrase or word list, and every string
constant in it is a vocabulary word from ``memory_claims`` (attributes, enums, field names) or a log code - pinned by an
AST test in ``tests/test_memory_claims_validate.py``. Phrasing enters ONLY through the model; the character-level helpers
(name parts, value normalisers) live in ``memory_claims``. Any failure is an abstain: the fact is left as mined.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

import memory_claims as mc
import people_graph
import structural_claims as sc

logger = logging.getLogger(__name__)
STATS: Counter = Counter()

# -- the injected inputs ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    """One user turn of the session, already filtered (speaker-rejected, own-words and forgotten turns dropped, C.1)."""
    t: int
    at: datetime
    text: str
    turn_id: str = ""
    own: bool = True            # the member's own words (False: code-authored or another speaker's, D.17)


@dataclass(frozen=True)
class Attr:
    subject_ref: Mapping
    attribute: str
    value_norm: Mapping
    unit: str
    span: Optional[str] = None


@dataclass(frozen=True)
class Row:
    id: str
    text: str
    status: str = mc.APPROVED
    entity_id: Optional[str] = None
    attrs: tuple = ()
    planned: bool = False       # a replacement this run planned: its attrs are code's own, no span to re-check


@dataclass(frozen=True)
class Registered:
    subject_ref: Mapping
    attribute: str
    role: str                   # superseded | asserted
    value_norm: Mapping
    said_at: datetime
    unit: str = ""
    by_claim: str = ""
    source_turn: str = ""
    row_ids: tuple = ()


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    relationship: str = ""


class Graph:
    """A snapshot of the member's people graph, read ONCE by the caller with a reader that RAISES on a failed read (D.5:
    ``people_graph.resolve_person`` answers "none" on a read error, which would turn an outage into "nobody by that
    name"). ``edges``: ``(person_id, kin code)`` of the member's CURRENT relationship edges. Resolution is
    ``people_graph.match_tier`` with ``allow_prefix=False`` - the same tiers as ``resolve_person``."""

    def __init__(self, people: Sequence[Person], edges: Sequence[tuple] = ()):
        self._people = tuple(people)
        self._by_id = {p.id: p for p in self._people}
        self._edges = tuple(edges)

    def resolve(self, name: str) -> people_graph.Resolution:
        tier, idx = people_graph.match_tier(name, [p.name for p in self._people], allow_prefix=False)
        if not idx:
            return people_graph.Resolution(mc.NONE, handle=name)
        found = tuple(people_graph.PersonMatch(self._people[i].id, self._people[i].name, self._people[i].relationship)
                      for i in idx)
        return people_graph.Resolution(mc.UNIQUE if len(found) == 1 else mc.AMBIGUOUS, tier, name, found)

    def related(self, kin: str) -> tuple:
        return tuple(dict.fromkeys(pid for pid, code in self._edges if code == kin))

    def relationship(self, person_id: str) -> str:
        p = self._by_id.get(person_id)
        return p.relationship if p else ""


@dataclass(frozen=True)
class Context:
    user_id: str
    turns: Sequence[Turn]
    rows: Sequence[Row]
    graph: Any                                       # ``Graph`` or any object with resolve / related / relationship
    registry: Sequence[Registered] = ()
    speaker_verified: Optional[bool] = None          # D.16: False = rejected
    identified: bool = True
    opted_out: bool = False
    retracted: bool = False                          # D.18: a turn of this session was retracted (turn_forget.blocks_write)
    forgotten: Optional[Callable[[str], bool]] = None   # D.18: the forgotten ledger's matcher
    erase_generation_scheduled: int = 0              # D.19
    erase_generation_now: int = 0
    detector: Optional[Callable[[str], Any]] = None  # D.15: ``medical_safety.detect`` (an object with ``cls`` / ``key``)


# -- the output ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowEdit:
    row_id: str
    attribute: str
    old: Mapping
    new: Mapping
    unit: str
    attrs: tuple                # the replacement row's attrs: this attribute's value changed, every other one as stored


@dataclass(frozen=True)
class EventPlan:
    subject_ref: Mapping
    event: str
    said_at: datetime


@dataclass(frozen=True)
class Verdict:
    verdict: str                # apply | agree | abstain | hold | refuse
    rule: Optional[int]         # the D rule that decided; None for an apply / a plain agree
    claim_type: str
    index: int
    claim_id: str = ""
    subject_ref: Optional[Mapping] = None
    attribute: str = ""
    said_at: Optional[datetime] = None
    edits: tuple = ()
    registry: tuple = ()
    event: Optional[EventPlan] = None
    important_date: bool = False                   # a birthday: person_important_dates is written too (A.6)
    actor: str = mc.WRITER_CLASS                   # D.17: the writer class
    anchor: Optional[tuple] = None                 # D.17: (turn, quote) - the member's own words authority is resolved on


_CLAIMS = (mc.CorrectionClaim, mc.KindClaim, mc.EmergencyClaim)
_PARSED = (*_CLAIMS, mc.Malformed)


class _Fail(Exception):
    def __init__(self, rule: int, verdict: str = mc.ABSTAIN):
        super().__init__(rule)
        self.rule, self.verdict = rule, verdict


def _need(ok: Any, rule: int, verdict: str = mc.ABSTAIN) -> None:
    if not ok:
        raise _Fail(rule, verdict)


# -- the gates (D.16-D.19) ----------------------------------------------------------------------------------------

def rule_16_speaker(ctx: Context) -> None:
    """The speaker is not rejected, is identified and is not opted out (value_correction.apply's checks)."""
    _need(ctx.speaker_verified is not False and ctx.identified and not ctx.opted_out, 16)


def rule_17_own_words(turn: Turn) -> None:
    """Authority is resolved on the member's own quote, never on code-authored text."""
    _need(turn.own, 17)


def rule_18_not_forgotten(claim: Any, ctx: Context) -> None:
    """No turn of the session was retracted; neither the subject nor the quote matches the forgotten ledger."""
    _need(not ctx.retracted, 18)
    if ctx.forgotten is not None:
        try:
            hit = ctx.forgotten(claim.subject) or ctx.forgotten(claim.quote)
        except Exception:  # noqa: BLE001 - a ledger that cannot be read is not "nothing forgotten"
            hit = True
        _need(not hit, 18)


def rule_19_erase_generation(ctx: Context) -> None:
    """The member's erase generation has not moved since the session was scheduled."""
    _need(ctx.erase_generation_scheduled == ctx.erase_generation_now, 19)


# -- the claim itself (D.1, D.2) ----------------------------------------------------------------------------------

def rule_1_quote(claim: Any, ctx: Context) -> Turn:
    """The quote is a substring of user turn ``claim.turn`` (``structural_claims.norm``: NFKC, casefold, whitespace fold);
    a correction's old and new values as said are inside the quote."""
    turn = next((t for t in ctx.turns if t.t == claim.turn), None)
    _need(turn is not None, 1)
    _need(sc.quote_basis(claim.quote, turn.text), 1)
    if isinstance(claim, mc.CorrectionClaim):
        q = sc.norm(claim.quote)
        for said in (claim.new, claim.old):
            _need(said is None or sc.norm(said) in q, 1)
    return turn


def rule_2_enums(claim: Any) -> None:
    """Type and attribute are in the closed enums (B.1, B.2; ``memory_claims.parse_claim`` enforced the shape); a
    correction is about NOW (a past or future value is no correction of the stored one)."""
    _need(not isinstance(claim, mc.Malformed), 2)
    if isinstance(claim, mc.CorrectionClaim):
        _need(claim.attribute in mc.ATTRIBUTES and claim.time == mc.CURRENT, 2)


# -- the subject (D.3-D.7) ----------------------------------------------------------------------------------------

def rule_5_resolve(name: str, ctx: Context) -> people_graph.Resolution:
    """A failed roster read is an abstain (the injected graph raises), never "nobody by that name"."""
    try:
        return ctx.graph.resolve(name)
    except Exception:  # noqa: BLE001
        raise _Fail(5) from None


def rule_3_tier(res: people_graph.Resolution) -> None:
    """Only the exact and whole-token tiers count: a prefix guess never resolves a subject."""
    _need(res.status == mc.NONE or res.tier in (mc.EXACT, mc.TOKEN), 3)


def rule_4_whole_parts(name: str, res: people_graph.Resolution) -> tuple:
    """Token tier: every query part must be a WHOLE name part with its hyphen kept ("ana" is not a part of "Ana-Lise").
    Returns the matches that survive."""
    if res.tier != mc.TOKEN:
        return tuple(res.matches)
    want = set(mc.name_parts(name))
    kept = tuple(m for m in res.matches if want <= set(mc.name_parts(m.name)))
    _need(kept, 4)
    return kept


def rule_6_relation(kin: str, ctx: Context, allow_label: bool) -> Mapping:
    """A relation subject resolves through exactly ONE current edge of that kin code; none is the label ``rel:<kin>``."""
    try:
        ids = tuple(ctx.graph.related(kin))
    except Exception:  # noqa: BLE001
        raise _Fail(5) from None
    _need(len(ids) <= 1, 6)
    if ids:
        return _person_ref(ids[0], ctx)
    _need(allow_label, 6)
    return {mc.REF_KIND: mc.LABEL, mc.REF_KEY: mc.relation_key(kin)}


def _typed_upto(phrase: str, upto: int, ctx: Context) -> bool:
    return any(t.t <= upto and t.own and mc.occurs_in(t.text, phrase) for t in ctx.turns)


def rule_6_in_session(name: str, upto: int, ctx: Context) -> bool:
    """A named subject was said by the member, as whole name parts, in a user turn at or before the quote's (B.3: the
    model may carry a name over a pronoun; code accepts it only if the member actually said it)."""
    return _typed_upto(mc.label_key(name), upto, ctx)


def rule_7_lazy_possessive(name: str, typed: bool, upto: int, ctx: Context) -> Optional[str]:
    """X typed ("kais") resolves none, Y ("Kai") resolves unique EXACT and fold(X) == fold(Y) + "s": the subject is Y.
    Never the reverse - an X that resolves wins. Returns the person id, or None."""
    if typed:
        x_forms = (name,)
        y = mc.possessive_base(name)
    else:
        y = people_graph.fold_name(name)
        x_forms = tuple(p for t in ctx.turns if t.t <= upto and t.own for p in mc.name_parts(t.text)
                        if mc.possessive_base(p) == y)
    if not y or not x_forms:
        return None
    if any(rule_5_resolve(x, ctx).status != mc.NONE for x in x_forms):
        return None
    res = rule_5_resolve(y, ctx)
    if res.status == mc.UNIQUE and res.tier == mc.EXACT:
        return res.matches[0].person_id
    return None


def _person_ref(person_id: str, ctx: Context) -> Mapping:
    try:
        rel = ctx.graph.relationship(person_id)
    except Exception:  # noqa: BLE001
        raise _Fail(5) from None
    return {mc.REF_KIND: mc.PET if mc.is_pet_relationship(rel) else mc.PERSON, mc.REF_ID: person_id}


def resolve_subject(subject: str, upto: int, ctx: Context, *, allow_label: bool, none_rule: int = 3) -> Mapping:
    """The subject_ref (B.3) of a subject as said, or a ``_Fail`` carrying the D rule."""
    form = mc.subject_form(subject)
    if form.form == mc.MEMBER:
        return {mc.REF_KIND: mc.MEMBER, mc.REF_ID: ctx.user_id}
    _need(form.form not in ("pronoun", "unknown"), 6)
    if form.form == "relation":
        return rule_6_relation(form.value, ctx, allow_label)
    name = form.value
    typed = rule_6_in_session(name, upto, ctx)
    res = rule_5_resolve(name, ctx)
    if res.status == mc.NONE or not typed:
        alias = rule_7_lazy_possessive(name, typed, upto, ctx)
        if alias is not None:
            return _person_ref(alias, ctx)
    _need(typed, 6)
    rule_3_tier(res)
    if res.status == mc.NONE:
        _need(allow_label, none_rule)
        return {mc.REF_KIND: mc.LABEL, mc.REF_KEY: mc.label_key(name)}
    kept = rule_4_whole_parts(name, res)
    _need(len(kept) == 1, 5)
    return _person_ref(kept[0].person_id, ctx)


def same_ref(a: Optional[Mapping], b: Optional[Mapping]) -> bool:
    if not a or not b:
        return False
    ka, kb = a.get(mc.REF_KIND), b.get(mc.REF_KIND)
    if ka == mc.LABEL or kb == mc.LABEL:
        return ka == kb and bool(a.get(mc.REF_KEY)) and a.get(mc.REF_KEY) == b.get(mc.REF_KEY)
    if ka == mc.MEMBER or kb == mc.MEMBER:
        return ka == kb and a.get(mc.REF_ID) == b.get(mc.REF_ID)
    return bool(a.get(mc.REF_ID)) and a.get(mc.REF_ID) == b.get(mc.REF_ID)     # person / pet: the id decides


# -- the value (D.8) ----------------------------------------------------------------------------------------------

def rule_8_values(claim: mc.CorrectionClaim, ref: Mapping, said_at: datetime, ctx: Context, rows: Sequence[Row]) -> tuple:
    """value_norm(new) parses under the attribute's normaliser with a unit in its unit set (a count is never an age, money
    never a date); an old value parses the same way; a partner resolves to one person; an age agrees with a stored
    birthday that carries a year."""
    ok, old, new = mc.normalise_pair(claim.attribute, claim.old, claim.new)
    _need(ok and new is not None and new.unit in mc.ATTRIBUTES[claim.attribute].units, 8)
    if claim.attribute == "partner":
        new = _partner(new, ctx)
        old = _partner(old, ctx) if old is not None else None
    if claim.attribute == "age":
        for row in _approved(rows):
            for a in row.attrs:
                if a.attribute == "birthday" and same_ref(a.subject_ref, ref):
                    years = mc.age_at(a.value_norm, said_at.date())
                    _need(years is None or int(new.value["v"]) == years, 8)
    return old, new


def _partner(n: mc.Norm, ctx: Context) -> mc.Norm:
    res = rule_5_resolve(n.value["name"], ctx)
    _need(res.status != mc.NONE and res.tier in (mc.EXACT, mc.TOKEN), 8)
    kept = rule_4_whole_parts(n.value["name"], res)
    _need(len(kept) == 1, 8)
    return mc.Norm({"person": kept[0].person_id}, n.unit)


# -- the stored rows (D.9-D.13, D.20) -----------------------------------------------------------------------------

def _approved(rows: Sequence[Row]) -> list:
    return [r for r in rows if r.status == mc.APPROVED]


def _attr(row: Row, ref: Mapping, attribute: str) -> Optional[Attr]:
    return next((a for a in row.attrs if a.attribute == attribute and same_ref(a.subject_ref, ref)), None)


def rule_12_candidates(ref: Mapping, attribute: str, rows: Sequence[Row]) -> list:
    """Each attribute is its own slot: an event_date claim only ever sees event_date attributes (never origin_date or a
    birthday), matched by subject_ref - never by the row's words."""
    return [r for r in _approved(rows) if _attr(r, ref, attribute) is not None]


def rule_20_spans(cands: Sequence[Row], ref: Mapping, attribute: str) -> None:
    """A stored attribute's span is a verbatim slice of the row text and its value_norm is what code computes from it."""
    for row in cands:
        if row.planned:
            continue
        a = _attr(row, ref, attribute)
        got = mc.span_value(attribute, row.text, a.span)
        _need(got is not None and mc.same_value(attribute, got.value, a.value_norm) and got.unit == a.unit, 20)


def rule_13_order(ref: Mapping, attribute: str, said_at: datetime, registry: Sequence[Registered]) -> None:
    """An older claim never overwrites a newer one: the claim must be newer than the latest 'asserted' entry for the
    same (subject, attribute)."""
    latest = [e.said_at for e in registry if e.role == mc.ASSERTED and e.attribute == attribute
              and same_ref(e.subject_ref, ref)]
    _need(not latest or said_at > max(latest), 13)


def rule_10_up_to_date(attribute: str, new: mc.Norm, ref: Mapping, cands: Sequence[Row]) -> list:
    """The rows whose stored value already IS the new one: nothing to write for them."""
    return [r for r in cands if mc.same_value(attribute, _attr(r, ref, attribute).value_norm, new.value)]


def rule_12_other_slot(attribute: str, old: mc.Norm, ref: Mapping, rows: Sequence[Row]) -> None:
    """The old value the claim names sits in ANOTHER slot of the same unit (an origin_date, a birthday) of the subject:
    that slot is not this claim's to move."""
    units = mc.ATTRIBUTES[attribute].units
    for row in _approved(rows):
        for a in row.attrs:
            if (a.attribute != attribute and a.attribute in mc.ATTRIBUTES and mc.ATTRIBUTES[a.attribute].units == units
                    and same_ref(a.subject_ref, ref)
                    and any(mc.old_matches(a.attribute, v, a.value_norm) for v in old.readings())):
                raise _Fail(12)


def rule_9_targets(claim: mc.CorrectionClaim, old: Optional[mc.Norm], ref: Mapping, cands: Sequence[Row],
                   fresh: Sequence[Row], rows: Sequence[Row] = ()) -> list:
    """Old said: the rows (1..MAX_ROWS twins) whose stored value the old one names. Old not said: exactly ONE candidate
    (none = nothing stored: a registry 'asserted' entry only). Employer and diet never move without an old value."""
    attribute = claim.attribute
    if old is None:
        _need(not mc.ATTRIBUTES[attribute].needs_old, 9)
        _need(len(cands) <= 1, 9)
        return [r for r in cands if r not in fresh]
    hits = _names_old(attribute, old, ref, [r for r in cands if r not in fresh])
    if not hits:
        rule_12_other_slot(attribute, old, ref, rows)
    _need(1 <= len(hits) <= mc.MAX_ROWS, 9)
    return hits


def rule_11_year(attribute: str, new: Mapping, stored: Mapping, said_at: datetime) -> Mapping:
    """A date restated without a year keeps the row's year only when the result is on or after the day it was said. A
    completed date that does not exist ("the 31st" over a February row) is no value at all (D.8)."""
    if attribute not in mc.DATE_ATTRIBUTES:
        return dict(new)
    merged, inherited = mc.merge_date(new, stored)
    _need(merged is not None, 8)
    _need(not inherited or mc.date_not_before(merged, said_at.date()), 11)
    return merged


def _new_value(attribute: str, new: mc.Norm, stored: Optional[Mapping], said_at: datetime) -> Mapping:
    if attribute == "time_of_day":
        resolved = mc.resolve_time(new.value, stored or {})
        _need(resolved is not None, 8)
        return resolved
    if stored is None:
        return dict(new.value)
    return rule_11_year(attribute, new.value, stored, said_at)


# -- the three claim classes --------------------------------------------------------------------------------------

def _correction(claim: mc.CorrectionClaim, index: int, ctx: Context, rows: Sequence[Row],
                registry: Sequence[Registered]) -> Verdict:
    turn = rule_1_quote(claim, ctx)
    rule_17_own_words(turn)
    ref = resolve_subject(claim.subject, turn.t, ctx, allow_label=True)
    said_at, attribute = turn.at, claim.attribute
    cid = mc.claim_id(ctx.user_id, turn.turn_id, index)
    base = Verdict(mc.APPLY, None, mc.CORRECTION, index, cid, ref, attribute, said_at, anchor=(turn.t, claim.quote))
    old, new = rule_8_values(claim, ref, said_at, ctx, rows)
    rule_13_order(ref, attribute, said_at, registry)
    cands = rule_12_candidates(ref, attribute, rows)
    rule_20_spans(cands, ref, attribute)
    fresh = rule_10_up_to_date(attribute, new, ref, cands)
    stale = [r for r in cands if r not in fresh]
    if fresh and (not stale if old is None else not _names_old(attribute, old, ref, stale)):
        return replace(base, verdict=mc.AGREE, rule=10)
    targets = rule_9_targets(claim, old, ref, cands, fresh, rows)
    edits, entries = [], []
    for row in targets:
        a = _attr(row, ref, attribute)
        value = _new_value(attribute, new, a.value_norm, said_at)
        attrs = tuple(replace(x, value_norm=value, span=None) if x is a else x for x in row.attrs)
        edits.append(RowEdit(row.id, attribute, a.value_norm, value, new.unit, attrs))
    if not targets:
        value = _new_value(attribute, new, None, said_at)
        assert_values = [value]
    else:
        assert_values = [e.new for e in edits]
    for old_value in _distinct([e.old for e in edits]):
        ids = tuple(e.row_id for e in edits if e.old == old_value)
        entries.append(Registered(ref, attribute, mc.SUPERSEDED, old_value, said_at, new.unit, cid, turn.turn_id, ids))
    for value in _distinct(assert_values):
        ids = tuple(e.row_id for e in edits if e.new == value)
        entries.append(Registered(ref, attribute, mc.ASSERTED, value, said_at, new.unit, cid, turn.turn_id, ids))
    important = attribute == "birthday" and ref.get(mc.REF_KIND) in (mc.PERSON, mc.PET)
    return replace(base, edits=tuple(edits), registry=tuple(entries), important_date=important)


def _names_old(attribute: str, old: mc.Norm, ref: Mapping, rows: Sequence[Row]) -> list:
    return [r for r in rows if any(mc.old_matches(attribute, v, _attr(r, ref, attribute).value_norm) for v in old.readings())]


def _distinct(values: Sequence[Mapping]) -> list:
    out: list = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def rule_14_kind(subject: str, kind: str, ctx: Context, rows: Sequence[Row], upto: Optional[int] = None) -> tuple:
    """KIND comes from the graph only: a people row whose relationship is ``pet`` / ``pet <kind>`` is an animal, any other
    (empty included) a person. ``(verdict, rule)``: agree = the graph says the same; refuse = it says otherwise (a person is
    never re-typed as an animal, nor the reverse); hold = two people share the name, or the name is not in the graph but a
    stored row names it (owner question J.2: held pending); abstain = an unknown name nobody stored (left as mined)."""
    form = mc.subject_form(subject)
    if form.form == mc.MEMBER:
        return (mc.AGREE, None) if kind == mc.PERSON else (mc.REFUSE, 14)
    if form.form == "relation":
        try:
            ids = tuple(ctx.graph.related(form.value))
        except Exception:  # noqa: BLE001
            return mc.ABSTAIN, 5
        if len(ids) != 1:
            return mc.ABSTAIN, 6
        pids = ids
    elif form.form == "name":
        if upto is not None and not rule_6_in_session(form.value, upto, ctx):
            return mc.ABSTAIN, 6
        try:
            res = rule_5_resolve(form.value, ctx)
            rule_3_tier(res)
            kept = rule_4_whole_parts(form.value, res) if res.status != mc.NONE else ()
        except _Fail as f:
            return (mc.ABSTAIN, f.rule) if f.rule != 4 else (_unknown_kind(form.value, rows), 14)
        if len(kept) > 1:
            return mc.HOLD, 14
        if not kept:
            return _unknown_kind(form.value, rows), 14
        pids = (kept[0].person_id,)
    else:
        return mc.ABSTAIN, 6
    try:
        graph_kind = mc.ANIMAL if mc.is_pet_relationship(ctx.graph.relationship(pids[0])) else mc.PERSON
    except Exception:  # noqa: BLE001
        return mc.ABSTAIN, 5
    return (mc.AGREE, None) if graph_kind == kind else (mc.REFUSE, 14)


def _unknown_kind(name: str, rows: Sequence[Row]) -> str:
    return mc.HOLD if any(mc.occurs_in(r.text, name) for r in _approved(rows)) else mc.ABSTAIN


def _kind(claim: mc.KindClaim, index: int, ctx: Context, rows: Sequence[Row]) -> Verdict:
    turn = rule_1_quote(claim, ctx)
    rule_17_own_words(turn)
    verdict, rule = rule_14_kind(claim.subject, claim.kind, ctx, rows, upto=turn.t)
    return Verdict(verdict, rule, mc.KIND, index, mc.claim_id(ctx.user_id, turn.turn_id, index), said_at=turn.at,
                   anchor=(turn.t, claim.quote))


def rule_15_emergency(claim: mc.EmergencyClaim, ref: Mapping, ctx: Context) -> None:
    """A health event is filed only when it is happening NOW, the safety detector reads the member's quote as a call-now
    emergency of the SAME kind, and the quote names no other person the graph knows."""
    _need(claim.time == mc.CURRENT and claim.event in mc.EVENTS and ctx.detector is not None, 15)
    try:
        v = ctx.detector(claim.quote)
        _need(v.cls == mc.DETECTOR_CALL and v.key == claim.event, 15)
    except _Fail:
        raise
    except Exception:  # noqa: BLE001
        raise _Fail(15) from None
    for part in mc.name_parts(claim.quote):
        res = rule_5_resolve(part, ctx)
        if res.status == mc.NONE or res.tier not in (mc.EXACT, mc.TOKEN):
            continue
        want = set(mc.name_parts(part))
        others = [m for m in res.matches if want <= set(mc.name_parts(m.name)) and m.person_id != ref.get(mc.REF_ID)]
        _need(not others, 15)


def _emergency(claim: mc.EmergencyClaim, index: int, ctx: Context) -> Verdict:
    turn = rule_1_quote(claim, ctx)
    rule_17_own_words(turn)
    _need(claim.time == mc.CURRENT, 15)
    ref = resolve_subject(claim.subject, turn.t, ctx, allow_label=False, none_rule=15)
    rule_15_emergency(claim, ref, ctx)
    return Verdict(mc.APPLY, None, mc.EMERGENCY, index, mc.claim_id(ctx.user_id, turn.turn_id, index), ref, "",
                   turn.at, event=EventPlan(ref, claim.event, turn.at), anchor=(turn.t, claim.quote))


# -- entry points -------------------------------------------------------------------------------------------------

def validate_claim(claim: Any, index: int, ctx: Context, rows: Optional[Sequence[Row]] = None,
                   registry: Optional[Sequence[Registered]] = None) -> Verdict:
    """One claim -> one Verdict. Never raises: an unexpected error is an abstain (rule 0)."""
    rows = ctx.rows if rows is None else rows
    registry = ctx.registry if registry is None else registry
    ctype = claim.type if isinstance(claim, _PARSED) else mc.NONE
    try:
        rule_16_speaker(ctx)
        rule_19_erase_generation(ctx)
        rule_2_enums(claim)
        rule_18_not_forgotten(claim, ctx)
        if isinstance(claim, mc.CorrectionClaim):
            return _correction(claim, index, ctx, rows, registry)
        if isinstance(claim, mc.KindClaim):
            return _kind(claim, index, ctx, rows)
        return _emergency(claim, index, ctx)
    except _Fail as f:
        return Verdict(f.verdict, f.rule, ctype, index)
    except Exception as exc:  # noqa: BLE001 - fail closed
        logger.debug(mc.LOG_INVALID, ctype, mc.ABSTAIN, type(exc).__name__, None)
        return Verdict(mc.ABSTAIN, 0, ctype, index)


def validate(output: Any, ctx: Context) -> tuple:
    """A model output (raw JSON, a mapping or a parsed ``memory_claims.Output``) -> one Verdict per claim, in emission
    order. Claims are decided OLDEST FIRST, each against the rows and registry as the earlier applies left them, so a
    chain inside one session (8 -> 7, 5th -> 12th -> 19th) is ordered by when it was said. An empty or abstained output
    is ``()``; a malformed envelope is one abstain on rule 2."""
    parsed = output if isinstance(output, mc.Output) else mc.parse_output(output)
    if parsed.error:
        return (Verdict(mc.ABSTAIN, 2, mc.NONE, 0),)
    at = {t.t: t.at for t in ctx.turns}
    order = sorted(range(len(parsed.items)), key=lambda i: (_said_key(at, parsed.items[i]), i))
    rows, registry = list(ctx.rows), list(ctx.registry)
    out: dict = {}
    for i in order:
        v = validate_claim(parsed.items[i], i, ctx, rows, registry)
        out[i] = v
        if v.verdict == mc.APPLY:
            rows = _fold(rows, v)
            registry.extend(v.registry)
    return tuple(out[i] for i in range(len(parsed.items)))


def _said_key(at: Mapping, claim: Any) -> tuple:
    when = at.get(claim.turn) if isinstance(claim, _CLAIMS) else None
    return (0, when.timestamp()) if when is not None else (1, 0.0)


def _fold(rows: list, v: Verdict) -> list:
    edited = {e.row_id: e for e in v.edits}
    out = []
    for r in rows:
        if r.id in edited:
            out.append(replace(r, status=mc.SUPERSEDED))
            out.append(Row(mc.planned_row_id(r.id, v.claim_id), r.text, mc.APPROVED, r.entity_id,
                           edited[r.id].attrs, True))
        else:
            out.append(r)
    return out


def is_stale(registry: Sequence[Registered], ref: Mapping, attribute: str, value_norm: Mapping,
             fact_said_at: datetime) -> bool:
    """Design note E: a mined fact is stale when a 'superseded' entry for the same (subject, attribute, value) was said
    AFTER the fact, and no 'asserted' entry for that value is newer than the supersession (12k -> 13.5k -> 12k)."""
    same = [e for e in registry if e.attribute == attribute and same_ref(e.subject_ref, ref)
            and mc.same_value(attribute, e.value_norm, value_norm)]
    for e in same:
        if e.role == mc.SUPERSEDED and e.said_at > fact_said_at:
            if not any(x.role == mc.ASSERTED and x.said_at > e.said_at for x in same):
                return True
    return False


def record(verdicts: Sequence[Verdict], lane: str) -> None:
    """Counts only (design note B.4): the class, verdict and rule of every claim that changed nothing. No subject, no value."""
    for v in verdicts:
        try:
            STATS[(v.claim_type, v.verdict, v.rule)] += 1
            if v.verdict not in (mc.APPLY, mc.AGREE):
                logger.info(mc.LOG_INVALID, v.claim_type or mc.NONE, v.verdict, v.rule, lane)
        except Exception:  # noqa: BLE001 - a log line never fails a pass
            pass
