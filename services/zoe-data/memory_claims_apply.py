"""Memory claims, THE ONE GUARDED WRITER (memory redesign step 6; ``ZOE_MEMORY_CLAIMS`` off | shadow | on, default off).

``apply`` takes ONE verdict of ``memory_claims_validate`` plus the claim it judged and is the only code that changes a stored
memory row for a claim. It writes through the writers that already guard memory - ``MemoryService.review(decision="edit")``
(new row first, then the old one retired; its own retraction, kind-flip, opt-out, forgotten-ledger and authority walls) and
``MemoryService.ingest`` for a filed health event - and adds, in this order:

  1. never without a validated ``apply`` verdict: an abstain / hold / refuse / agree writes nothing;
  2. the new sentence is ``memory_claims_render.splice`` (the member's own words inside the stored span, read back by the
     normalisers); ``value_correction.drops_value`` ("never less") refuses a text that would lose a value; a claim whose rows
     cannot ALL be planned changes none of them;
  3. the forget walls, fail closed: a retracted turn (``turn_forget``), the forgotten ledger read STRICTLY on the quote, the
     turn and every new text (an unreadable ledger refuses), and the member's erase generation held through the write;
  4. authority is never lowered: the new row's rank (``memory_authority.resolve_write`` for the correction writer on the
     member's own turn) must be at least the stored row's, so a ``user_confirmed``, operator or curated row is never
     replaced by a claim, and ``may_override`` must agree;
  5. write, read both rows back, and only then record the superseded value and the structured birthday.

``shadow`` computes all of that except 5 and writes nothing: the pass that calls it logs one counts-only
``MEMORY_CLAIM_SHADOW`` line against what the old regex path (``value_correction``) would do. ``on`` applies CORRECTIONS;
a health event is filed only when the caller names the class (``classes``), because emergency flips last.

Logs carry codes and counts only: no member, no subject, no value, no text.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Sequence

import memory_claims as mc
import memory_claims_render as render
import memory_claims_validate as vm

logger = logging.getLogger(__name__)

FLAG = "ZOE_MEMORY_CLAIMS"
OFF, SHADOW, ON = "off", "shadow", "on"
WRITER = mc.WRITER_CLASS
ATTRS_KEY = "claim_attrs"                 # review / ingest store it ``candidate_``-prefixed, with the row (design note F)
STORED_ATTRS_KEY = "candidate_" + ATTRS_KEY
ON_CLASSES = (mc.CORRECTION,)
NOTE = "validated claim (memory_claims_apply)"
STATS: Counter = Counter()

LOG_APPLY = "MEMORY_CLAIM_APPLY class=%s mode=%s code=%s rows=%d registry=%d"
LOG_SHADOW = "MEMORY_CLAIM_SHADOW class=%s lane=%s verdict=%s rule=%s rows_old=%d rows_claim=%d ms=%d model=%s"

# outcome codes
C_OFF, C_NOT_VALIDATED, C_NOTHING, C_WOULD, C_WRITTEN = "off", "not_validated", "nothing_to_write", "would_write", "written"
C_RENDER, C_LESS, C_STALE_ROW, C_RETRACTED, C_FORGOTTEN, C_LEDGER = ("render", "drops_value", "stale_row", "retracted",
                                                                    "forgotten", "ledger_unreadable")
C_AUTHORITY, C_ERASED, C_REFUSED, C_VERIFY, C_ERROR = "authority", "erased", "write_refused", "verify_failed", "error"


def mode() -> str:
    try:
        import typed_env

        return str(typed_env.flag(FLAG))
    except Exception:  # noqa: BLE001 - an unreadable flag is OFF
        return OFF


@dataclass(frozen=True)
class Job:
    """What the pass already holds for one session. ``generation``: ``user_erase_gate.generation`` when the session was
    PICKED. ``people``: person id -> (name, relationship), for a health event's text only."""
    user_id: str
    turns: Sequence[vm.Turn]
    rows: Sequence[vm.Row]
    generation: int = 0
    session_id: Optional[str] = None
    speaker_verified: Optional[bool] = None
    people: Optional[Mapping] = None


@dataclass(frozen=True)
class Plan:
    row_id: str
    old_text: str
    new_text: str
    attrs: tuple


@dataclass(frozen=True)
class Outcome:
    code: str
    rows: int = 0
    registry: int = 0
    plans: tuple = ()           # in memory only (the shadow comparison); never logged
    new_ids: tuple = ()

    @property
    def writes(self) -> bool:
        return self.code in (C_WOULD, C_WRITTEN)


class _Refuse(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# -- seams (tests and the lab replace them) -----------------------------------------------------------------------

class Walls:
    """The forget walls. ``forgotten`` RAISES when the ledger cannot be read."""

    def retracted(self, user_id: str, text: str) -> bool:
        import turn_forget

        return bool(turn_forget.blocks_write(user_id, text))

    async def forgotten(self, user_id: str, text: str) -> bool:
        import memory_forgotten

        return bool(await memory_forgotten.matches(user_id, text, near=True, strict=True))


class ValueRegistry:
    """The superseded-value registry (``memory_value_registry``, migration 0080). That module is another change: when it is
    not importable nothing is recorded and the count says so. It keys a subject by ``self`` or a person id, so a label
    subject ("book club") is not recordable there."""

    async def record(self, user_id: str, entries: Sequence[vm.Registered], *, generation: int, row_ids: Mapping) -> int:
        try:
            import memory_value_registry as reg
        except ImportError:
            STATS["registry_absent"] += 1
            return 0
        done, groups = 0, {}
        for e in entries:
            kind = e.subject_ref.get(mc.REF_KIND)
            ref = reg.SELF if kind == mc.MEMBER else e.subject_ref.get(mc.REF_ID) if kind in (mc.PERSON, mc.PET) else None
            if ref is None or e.attribute not in reg.ATTRIBUTES:
                STATS["registry_unrecordable"] += 1
                continue
            groups.setdefault((ref, e.attribute, e.by_claim), []).append(e)
        for (ref, attribute, by_claim), es in groups.items():
            asserted = [e.value_norm for e in es if e.role == mc.ASSERTED]
            old_ids = [i for e in es for i in e.row_ids]
            ids = old_ids + [row_ids[i] for i in old_ids if i in row_ids]      # the retired rows and their replacements
            done += await reg.record(
                user_id, ref, attribute, superseded=[e.value_norm for e in es if e.role == mc.SUPERSEDED],
                asserted=asserted[-1] if asserted else None, said_at=es[0].said_at.timestamp(), by_claim=by_claim,
                source_turn_id=es[0].source_turn or None, row_ids=ids, scheduled_at=generation)
        return done


async def fix_important_date(user_id: str, old_id: str, new_id: str, value: Mapping) -> bool:
    """A corrected birthday also moves the person's structured date (``person_important_dates``, design note A.6)."""
    import correction_apply
    import person_extractor

    if "m" not in value or "d" not in value:
        return False
    db, opened = await person_extractor._ensure_db(None)
    if db is None:
        return False
    try:
        await correction_apply._fix_structured_date(
            db, user_id, old_id, new_id, SimpleNamespace(month=value["m"], day=value["d"], year=value.get("y")))
    finally:
        if opened:
            await db.close()
    return True


# -- planning (pure) ----------------------------------------------------------------------------------------------

def _turn(job: Job, verdict: vm.Verdict) -> vm.Turn:
    t = next((x for x in job.turns if verdict.anchor and x.t == verdict.anchor[0]), None)
    if t is None or not t.own:
        raise _Refuse(C_NOT_VALIDATED)
    return t


def plan_edits(verdict: vm.Verdict, claim: Any, rows: Sequence[vm.Row]) -> tuple:
    """One ``Plan`` per row edit, or ``_Refuse``: all the rows of a claim move, or none does."""
    from value_correction import drops_value

    by_id = {r.id: r for r in rows}
    out = []
    for e in verdict.edits:
        row = by_id.get(e.row_id)
        stored = next((a for a in (row.attrs if row else ()) if a.attribute == e.attribute
                       and vm.same_ref(a.subject_ref, verdict.subject_ref)), None)
        if stored is None or row.status != mc.APPROVED:
            raise _Refuse(C_STALE_ROW)
        made = render.splice(e.attribute, row.text, stored.span, e.old, e.new, e.unit, claim.new)
        if made is None:
            raise _Refuse(C_RENDER)
        new_text, new_span = made
        if new_text == row.text or drops_value(row.text, new_text):
            raise _Refuse(C_LESS)
        a, b = mc.parse_span(stored.span)
        delta = len(new_text) - len(row.text)
        attrs = tuple(
            vm.Attr(x.subject_ref, x.attribute, e.new if x is stored else x.value_norm, x.unit,
                    new_span if x is stored else render.shift_span(x.span, a, b, delta))
            for x in row.attrs)
        out.append(Plan(row.id, row.text, new_text, attrs))
    return tuple(out)


def event_text(verdict: vm.Verdict, job: Job) -> str:
    """The ONE dated health event a validated emergency claim is filed as - ``medical_safety.emergency_event``'s sentence,
    with the person from the people graph instead of from the fact's words."""
    import medical_safety

    ev = verdict.event
    ref = ev.subject_ref
    who = "User"
    if ref.get(mc.REF_KIND) != mc.MEMBER:
        name, rel = (job.people or {}).get(ref.get(mc.REF_ID)) or ("", "")
        if not name:
            raise _Refuse(C_RENDER)
        who = f"User's {rel} {name}" if rel and not mc.is_pet_relationship(rel) else name
    d = ev.said_at.date()
    return f"{who} had {medical_safety._EVENT_WHAT[ev.event]} on {d.day} {d:%B %Y} (a medical emergency)."


def attrs_json(attrs: Sequence[vm.Attr]) -> str:
    return json.dumps([{"subject_ref": dict(a.subject_ref), "attribute": a.attribute, "value_norm": dict(a.value_norm),
                        "unit": a.unit, "span": a.span, "v": 1} for a in attrs], sort_keys=True, separators=(",", ":"))


def attrs_of(metadata: Optional[Mapping]) -> tuple:
    """The attributes a stored row carries (``metadata[candidate_claim_attrs]``); ``()`` for none or an unreadable value."""
    try:
        raw = json.loads((metadata or {}).get(STORED_ATTRS_KEY) or "[]")
        return tuple(vm.Attr(a["subject_ref"], a["attribute"], a["value_norm"], a.get("unit") or "", a.get("span"))
                     for a in raw)
    except Exception:  # noqa: BLE001
        return ()


# -- the walls ----------------------------------------------------------------------------------------------------

async def _forget_walls(job: Job, turn: vm.Turn, quote: str, texts: Sequence[str], walls: Any) -> None:
    for said in (turn.text, quote):
        if walls.retracted(job.user_id, said):
            raise _Refuse(C_RETRACTED)
    for text in (quote, turn.text, *texts):
        try:
            hit = await walls.forgotten(job.user_id, text)
        except Exception:  # noqa: BLE001 - a ledger that cannot be read is not "nothing forgotten"
            raise _Refuse(C_LEDGER) from None
        if hit:
            raise _Refuse(C_FORGOTTEN)


async def _authority(job: Job, turn: vm.Turn, plans: Sequence[Plan], svc: Any) -> dict:
    """The stored rank of every row to be replaced; ``_Refuse`` when a row moved since it was read or outranks the claim."""
    import memory_authority as auth

    ranks = {}
    for p in plans:
        cur = await svc.get(p.row_id)
        meta = dict(getattr(cur, "metadata", None) or {})
        if cur is None or (cur.text or "") != p.old_text or str(meta.get("status") or mc.APPROVED) != mc.APPROVED:
            raise _Refuse(C_STALE_ROW)
        new = auth.resolve_write(WRITER, p.new_text, anchor_text=turn.text, user_id=job.user_id,
                                 speaker_verified=job.speaker_verified)
        ranks[p.row_id] = auth.row_rank(meta, cur.text or "")
        if new.rank < ranks[p.row_id] or not auth.may_override(new.power, auth.row_class(meta, cur.text or "")):
            raise _Refuse(C_AUTHORITY)
    return ranks


# -- the writes ---------------------------------------------------------------------------------------------------

async def _write_rows(job: Job, turn: vm.Turn, plans: Sequence[Plan], ranks: Mapping, svc: Any) -> dict:
    """review(edit) each row, then READ BOTH ROWS BACK: old id -> new id. ``_Refuse`` on the first row that is refused or
    does not read back as written (rows already replaced stay replaced: each is a complete, verified correction)."""
    import memory_authority as auth

    done = {}
    extra = {} if job.speaker_verified is None else {"speaker_verified": job.speaker_verified}
    for p in plans:
        ref = await svc.review(p.row_id, decision="edit", edits=p.new_text, actor=WRITER, note=NOTE,
                               source_excerpt=turn.text, anchor_text=turn.text, session_id=job.session_id,
                               metadata={ATTRS_KEY: attrs_json(p.attrs)}, **extra)
        if ref is None:
            raise _Refuse(C_REFUSED)
        new, old = await svc.get(ref.id), await svc.get(p.row_id)
        nm = dict(getattr(new, "metadata", None) or {})
        ok = (new is not None and (new.text or "") == p.new_text and str(nm.get("status")) == mc.APPROVED
              and auth.row_rank(nm, new.text or "") >= ranks[p.row_id]
              and (ref.id == p.row_id or (old is not None and str((old.metadata or {}).get("status")) == mc.SUPERSEDED)))
        if not ok:
            raise _Refuse(C_VERIFY)
        done[p.row_id] = ref.id
    return done


async def _file_event(job: Job, turn: vm.Turn, text: str, verdict: vm.Verdict, svc: Any) -> str:
    ref = verdict.event.subject_ref
    person = ref.get(mc.REF_ID) if ref.get(mc.REF_KIND) in (mc.PERSON, mc.PET) else None
    extra = {} if job.speaker_verified is None else {"speaker_verified": job.speaker_verified}
    got = await svc.ingest(text, user_id=job.user_id, source=WRITER, session_id=job.session_id, memory_type="event",
                           tags=["health", "medical"], entity_type="person" if person else None, entity_id=person,
                           source_excerpt=turn.text, anchor_text=turn.text, **extra)
    if got is None:
        raise _Refuse(C_REFUSED)
    back = await svc.get(got.id)
    if back is None or str((back.metadata or {}).get("memory_type")) != "event":
        raise _Refuse(C_VERIFY)
    return got.id


def _done(verdict: Any, m: str, out: Outcome) -> Outcome:
    STATS[(getattr(verdict, "claim_type", "") or mc.NONE, m, out.code)] += 1
    logger.info(LOG_APPLY, getattr(verdict, "claim_type", "") or mc.NONE, m, out.code, out.rows, out.registry)
    return out


async def apply(verdict: vm.Verdict, claim: Any, job: Job, *, svc: Any = None, registry: Any = None, walls: Any = None,
                fix_date: Any = fix_important_date, mode_: Optional[str] = None,
                classes: Sequence[str] = ON_CLASSES) -> Outcome:
    """Apply ONE validated claim (see the module text). Returns what happened as a code and counts. Never raises."""
    m = mode() if mode_ is None else mode_
    if m not in (SHADOW, ON):
        return Outcome(C_OFF)
    try:
        if not isinstance(verdict, vm.Verdict) or verdict.verdict != mc.APPLY or verdict.rule is not None:
            return _done(verdict, m, Outcome(C_NOT_VALIDATED))
        import user_erase_gate

        turn = _turn(job, verdict)
        quote = verdict.anchor[1]
        is_event = verdict.event is not None
        plans = () if is_event else plan_edits(verdict, claim, job.rows)
        text = event_text(verdict, job) if is_event else ""
        if not is_event and not plans and not verdict.registry:
            return _done(verdict, m, Outcome(C_NOTHING))
        await _forget_walls(job, turn, quote, [text] if is_event else [p.new_text for p in plans],
                            walls if walls is not None else Walls())
        if svc is None:
            from memory_service import get_memory_service

            svc = get_memory_service()
        ranks = await _authority(job, turn, plans, svc)
        n = 1 if is_event else len(plans)
        if m != ON or verdict.claim_type not in classes:
            return _done(verdict, m, Outcome(C_WOULD, n, len(verdict.registry), plans))
        async with user_erase_gate.writing(job.user_id, job.generation) as live:
            if not live:
                raise _Refuse(C_ERASED)
            if is_event:
                return _done(verdict, m, Outcome(C_WRITTEN, 1, 0, (), (await _file_event(job, turn, text, verdict, svc),)))
            ids = await _write_rows(job, turn, plans, ranks, svc)
            recorded = 0
            if verdict.registry:
                recorded = await (registry if registry is not None else ValueRegistry()).record(
                    job.user_id, verdict.registry, generation=job.generation, row_ids=ids)
            if verdict.important_date and fix_date is not None:
                for e in verdict.edits:
                    try:
                        await fix_date(job.user_id, e.row_id, ids[e.row_id], e.new)
                    except Exception as exc:  # noqa: BLE001 - the memory row is corrected; the structured twin is reported
                        STATS["important_date_failed"] += 1
                        logger.warning("MEMORY_CLAIM_APPLY important date not moved (%s)", type(exc).__name__)
        return _done(verdict, m, Outcome(C_WRITTEN, len(ids), recorded, plans, tuple(ids.values())))
    except _Refuse as r:
        return _done(verdict, m, Outcome(r.code))
    except Exception as exc:  # noqa: BLE001 - fail closed: nothing further is written
        logger.warning("MEMORY_CLAIM_APPLY failed (%s)", type(exc).__name__)
        return _done(verdict, m, Outcome(C_ERROR))


# -- the shadow comparison with the old regex path ----------------------------------------------------------------

def old_path_plan(turn_text: str, rows: Sequence[vm.Row]) -> dict:
    """What ``value_correction`` would rewrite for this turn: row id -> new text. Its own pure functions (``statement`` +
    ``rewrite``), nothing remembered and nothing written; more than ``MAX_ROWS`` hits is its "rewrite none". Never raises."""
    try:
        import value_correction as vc

        corr = vc.statement(turn_text or "")
        if corr is None:
            return {}
        hits = {}
        for r in rows:
            if r.status == mc.APPROVED and not r.planned:
                new = vc.rewrite(r.text or "", corr)
                if new and new != r.text:
                    hits[r.id] = new
        return hits if len(hits) <= vc.MAX_ROWS else {}
    except Exception:  # noqa: BLE001
        return {}


def compare(old: Mapping, claim: Mapping) -> str:
    """agree | claim_only | old_only | disagree | abstain (design note H), from two ``row id -> new text`` plans."""
    if not old and not claim:
        return mc.ABSTAIN
    if not old:
        return "claim_only"
    if not claim:
        return "old_only"
    return mc.AGREE if dict(old) == dict(claim) else "disagree"


def log_shadow(claim_type: str, lane: str, verdict: str, rule: Optional[int], rows_old: int, rows_claim: int, ms: int,
               model: str) -> None:
    STATS[("shadow", claim_type, verdict)] += 1
    logger.info(LOG_SHADOW, claim_type or mc.NONE, lane, verdict, "-" if rule is None else rule, rows_old, rows_claim, ms,
                model)
