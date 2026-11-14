"""Memory claims, THE IDLE CLAIM PASS (memory redesign step 7; ``ZOE_MEMORY_CLAIMS`` off | shadow | on, default off).

Ticked by the idle-consolidation loop (``memory_idle_consolidation``), never by a turn. For a session that has gone quiet and
holds a turn the pending-correction marker or the old correction detector flagged, it asks the brain ONCE for a constrained
JSON claim (``memory_claims`` schema), reads the attributes of the few stored rows the claim could touch (a second, BLIND
call: the model sees the rows, not the correction), validates with ``memory_claims_validate`` and hands every verdict to the
one guarded writer (``memory_claims_apply``). In ``shadow`` nothing is written: one counts-only ``MEMORY_CLAIM_SHADOW`` line
per flagged turn says whether the claim path and the old regex path agree.

Bounded: ``MAX_TURNS_PER_PASS`` flagged turns and ``TIME_BUDGET_S`` per sweep, at most two brain calls per session,
``MAX_PER_HOUR`` calls, a breaker after ``BREAKER_AFTER`` failures. A session with no flagged turn costs no model call.

THE BRAIN SLOT. The brain runs ONE llama slot (``--parallel 1``, pinned by the unit-flag test): there is no second slot or
session id that would keep a helper's prompt apart from the panel's. So this pass protects the panel's prefix by SCHEDULING,
exactly as the other background readers do: every call goes through ``brain_slot_guard.run_background`` (waits for a quiet
conversation, is cancelled the moment a household turn starts, and counts as a waiter so ``brain_prefix_keeper`` re-loads
the household prefix after the batch), it is named to ``brain_scheduler`` (``LABEL``) and it asks the scheduler FIRST: a
call the scheduler would not allow is not made and not counted. It never pins ``id_slot`` and never talks to the Flue
sidecar, so it shares no session with a panel. Whether ``PANEL_KV_MISS`` moves is a live measurement, not a property of
this file.

Model, people-graph, memory and chat reads sit behind ``Source`` / ``Reader`` seams; tests and the lab replace them.
Logs carry codes and counts only.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Sequence

import memory_claims as mc
import memory_claims_apply as ap
import memory_claims_validate as vm

logger = logging.getLogger(__name__)

LABEL = "memory_claims_pass"
LANE, MODEL = "idle", "brain"
MAX_TURNS_PER_PASS = 3        # flagged turns read per sweep (N)
MAX_SESSIONS = 6
TIME_BUDGET_S = 60.0
MAX_SESSION_TURNS = 12        # the newest user turns of a session the model is shown
MAX_ROWS_READ = 12            # stored rows a blind attribute read is shown
MAX_TOKENS, ROWS_MAX_TOKENS, TIMEOUT_S = 256, 384, 20.0
MAX_PER_HOUR, BREAKER_AFTER, BREAKER_S = 30, 3, 300.0
HANDLED_MAX = 512
STATS: collections.Counter = collections.Counter()
LOG_PASS = "MEMORY_CLAIM_PASS mode=%s sessions=%d flagged=%d calls=%d claims=%d skipped=%s ms=%d"

_calls: collections.deque = collections.deque()
_handled: "collections.OrderedDict[str, float]" = collections.OrderedDict()
_fail = 0
_open_until = 0.0

SYSTEM = "You read corrections a person made about things they told an assistant. Return only JSON."
ROWS_SYSTEM = "You label the values in stored notes about a person's life. Return only JSON."


def reset_state() -> None:
    global _fail, _open_until
    _calls.clear()
    _handled.clear()
    STATS.clear()
    _fail, _open_until = 0, 0.0


# -- the prompts and the schemas (design note C.1 / C.2) -----------------------------------------------------------

def _enum(values: Sequence[str]) -> dict:
    return {"type": "string", "enum": list(values)}


def _text() -> dict:
    return {"type": "string", "maxLength": mc.MAX_TEXT}


def claim_schema() -> dict:
    base = {"turn": {"type": "integer"}, "quote": _text(), "subject": _text()}

    def item(kind: str, extra: dict) -> dict:
        props = {"type": {"const": kind}, **base, **extra}
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    return {"type": "object", "additionalProperties": False, "required": ["claims", "abstain"], "properties": {
        "claims": {"type": "array", "maxItems": mc.MAX_CLAIMS, "items": {"oneOf": [
            item(mc.CORRECTION, {"attribute": _enum(tuple(mc.ATTRIBUTES)), "old": {"type": ["string", "null"], "maxLength": mc.MAX_TEXT},
                                 "new": _text(), "time": _enum(mc.CORRECTION_TIMES)}),
            item(mc.KIND, {"kind": _enum(mc.KINDS)}),
            item(mc.EMERGENCY, {"event": _enum(mc.EVENTS), "time": _enum(mc.EMERGENCY_TIMES)})]}},
        "abstain": {"enum": [None, *mc.ABSTAIN_REASONS]}}}


def rows_schema() -> dict:
    item = {"type": "object", "additionalProperties": False, "required": ["row", "subject", "attribute", "span"],
            "properties": {"row": {"type": "integer"}, "subject": _text(), "attribute": _enum(tuple(mc.ATTRIBUTES)),
                           "span": _text()}}
    return {"type": "object", "additionalProperties": False, "required": ["rows"],
            "properties": {"rows": {"type": "array", "maxItems": 2 * MAX_ROWS_READ, "items": item}}}


def claim_prompt(turns: Sequence[vm.Turn], flagged: Sequence[int]) -> str:
    from date_locale import normalize_numeric_dates

    lines = [f"[{t.t}] {normalize_numeric_dates(t.text)}" for t in turns]
    return ("What the person said, one numbered turn per line:\n" + "\n".join(lines)
            + f"\n\nTurns that may hold a correction: {', '.join(str(i) for i in flagged)}.\n"
            "For each correction give: turn (its number), quote (copied exactly from that turn), subject (who or what it is "
            "about, as said; a name the person used earlier may stand for she / he / it), attribute (one of: "
            + ", ".join(mc.ATTRIBUTES) + "), old (the value corrected away as said, or null), new (the value now, as said), "
            "time (current, past or future).\n"
            'Example: "[0] juno is 8 not 7" -> {"claims":[{"type":"correction","turn":0,"quote":"juno is 8 not 7",'
            '"subject":"Juno","attribute":"age","old":"7","new":"8","time":"current"}],"abstain":null}\n'
            'Example: "[0] tea, not coffee" -> {"claims":[],"abstain":"no_correction"}\n'
            "Copy quote exactly. If you are not sure who or what, answer abstain.")


def rows_prompt(texts: Sequence[str]) -> str:
    return ("Stored notes, one numbered note per line:\n" + "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
            + "\n\nFor every value in a note give: row (its number), subject (who or what the value belongs to, as written), "
            "attribute (one of: " + ", ".join(mc.ATTRIBUTES) + "), span (the value copied exactly from the note).\n"
            'Example: "[0] User\'s daughter Juno is 7 years old." -> {"rows":[{"row":0,"subject":"Juno","attribute":"age",'
            '"span":"7 years old"}]}\nCopy span exactly. Leave out anything you are not sure of.')


# -- seams ---------------------------------------------------------------------------------------------------------

class BrainReader:
    """The two constrained calls on the brain's llama-server (``response_format`` json_schema, strict), each through the
    slot gate under ``LABEL``. Raises on any failure: the pass then treats the read as "returned nothing"."""

    async def _ask(self, system: str, user: str, schema: dict, name: str, max_tokens: int) -> str:
        import httpx

        import brain_slot_guard
        from memory_digest import _GEMMA_URL

        payload = {"model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "max_tokens": max_tokens, "temperature": 0, "stream": False, "cache_prompt": True,
                   "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}

        async def _llm() -> str:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                return str(resp.json()["choices"][0]["message"]["content"] or "").strip()

        return await brain_slot_guard.run_background(LABEL, _llm)

    async def claims(self, turns: Sequence[vm.Turn], flagged: Sequence[int]) -> str:
        return await self._ask(SYSTEM, claim_prompt(turns, flagged), claim_schema(), "memory_claims", MAX_TOKENS)

    async def attributes(self, texts: Sequence[str]) -> str:
        return await self._ask(ROWS_SYSTEM, rows_prompt(texts), rows_schema(), "memory_row_attributes", ROWS_MAX_TOKENS)


def graph_of(people: Sequence[tuple]) -> vm.Graph:
    """``(id, name, relationship)`` rows -> the validator's graph. A kin edge is the people row's own relationship word read
    through the kin lexicon (``lexicons.kin_code``): "dad" and "father" are one code."""
    import lexicons

    persons = [vm.Person(str(i), str(n or "").strip(), str(r or "").strip()) for i, n, r in people]
    edges = []
    for p in persons:
        words = p.relationship.casefold().split()
        code = lexicons.kin_code(words[-1]) if words and not mc.is_pet_relationship(p.relationship) else None
        if code:
            edges.append((p.id, code))
    return vm.Graph(persons, edges)


class Source:
    """Production reads. Every one RAISES on failure (the pass then skips the session: an outage is never "nobody by that
    name" or "nothing stored")."""

    async def sessions(self) -> list:
        """``[{session_id, user_id}]``: sessions with a turn in the idle lookback that have been quiet for the idle time."""
        import memory_idle_consolidation as idle
        from db_pool import get_db_ctx

        async with get_db_ctx() as conn:
            rows = await conn.fetch(
                "SELECT m.session_id AS session_id, max(s.user_id) AS user_id FROM chat_messages m "
                "LEFT JOIN chat_sessions s ON s.id = m.session_id "
                "WHERE m.created_at::timestamptz > now() - ($1::int * interval '1 second') GROUP BY m.session_id "
                "HAVING max(m.created_at::timestamptz) < now() - ($2::int * interval '1 second') "
                "ORDER BY max(m.created_at::timestamptz) DESC LIMIT $3::int",
                idle.LOOKBACK_SECONDS, idle.IDLE_SECONDS, 4 * MAX_SESSIONS)
        return [{"session_id": r["session_id"], "user_id": r["user_id"]} for r in rows]

    async def turns(self, session_id: str) -> list:
        """The session's on-the-record chat rows, oldest first: mappings with id, role, content, metadata, at."""
        from db_pool import get_db_ctx

        async with get_db_ctx() as conn:
            return list(await conn.fetch(
                "SELECT id, role, content, metadata, created_at::timestamptz AS at FROM chat_messages WHERE session_id = $1 "
                "AND COALESCE(metadata, '') NOT LIKE '%\"off_record\": true%' ORDER BY created_at::timestamptz", session_id))

    async def graph(self, user_id: str) -> vm.Graph:
        import people_graph
        import person_extractor

        db, opened = await person_extractor._ensure_db(None)
        if db is None:
            raise RuntimeError("people store unavailable")
        try:
            rows = await people_graph._all(db, "SELECT id, name, relationship FROM people WHERE user_id = ? AND deleted = 0",
                                           (user_id,))
        finally:
            if opened:
                await db.close()
        return graph_of([(r[0], r[1], r[2]) for r in rows])

    async def rows(self, user_id: str) -> list:
        from memory_service import get_memory_service

        return list(await get_memory_service().list_all_by_status_strict(user_id=user_id, status=mc.APPROVED))

    def marked(self, user_id: str) -> frozenset:
        """Chat-message ids the pending-correction marker holds for the member (empty when that module is not present)."""
        try:
            import correction_marker
        except ImportError:
            return frozenset()
        return frozenset(m.turn_id for m in correction_marker.pending(user_id))

    def clear_marker(self, user_id: str, message_id: str) -> None:
        try:
            import correction_marker

            correction_marker.clear(user_id, message_id)
        except Exception:  # noqa: BLE001 - the marker also expires on its own
            pass


def detector_flags(text: str) -> bool:
    """The OLD correction detector, as a trigger only (it has no write power here)."""
    try:
        import memory_quality
        import value_correction

        return value_correction.statement(text) is not None or bool(memory_quality.looks_like_correction(text))
    except Exception:  # noqa: BLE001
        return False


# -- one session ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: str
    turns: tuple                # vm.Turn, filtered
    flagged: tuple              # turn numbers still to read
    message_ids: Mapping        # turn number -> chat message id
    generation: int


async def load_session(src: Any, session_id: str, session_user: Any, budget: int) -> Optional[Session]:
    """The session's filtered user turns and which of them are flagged and not yet handled; ``None`` = nothing to read.
    Filters as idle consolidation does: speaker-rejected turns dropped, the owner's own words only, forgotten turns skipped
    (a ledger that cannot be read RAISES, so the session is retried later)."""
    import memory_idle_consolidation as idle
    import own_words
    import user_erase_gate
    from correction_apply import _is_unidentified
    from memory_digest import _skip_forgotten_turns
    from people_graph import content_turn_id

    raw = await src.turns(session_id)
    user_id = idle._resolve_owner(raw, session_user)
    if not user_id or _is_unidentified(user_id):
        return None
    generation = user_erase_gate.generation(user_id)
    marked = src.marked(user_id)
    turns, flagged, ids = [], [], {}
    for r in [x for x in raw if str(x["role"]) == "user" and x["content"]][-MAX_SESSION_TURNS:]:
        if idle._speaker_rejected(r["metadata"]):
            continue
        kept = own_words.filter_turns([str(r["content"])], LABEL)
        kept = await _skip_forgotten_turns(user_id, kept, LABEL) if kept else []
        if not kept:
            continue
        n, text = len(turns), kept[0]
        tid = content_turn_id(user_id, text) or ""
        turns.append(vm.Turn(n, r["at"], text, tid))
        ids[n] = str(r["id"])
        if (str(r["id"]) in marked or detector_flags(text)) and f"{user_id}|{tid}" not in _handled:
            flagged.append(n)
    if not flagged:
        return None
    return Session(session_id, user_id, tuple(turns), tuple(flagged[-budget:]), ids, generation)


def candidate_rows(refs: Sequence[Any], claims: Sequence[Any], graph: vm.Graph) -> list:
    """The member's approved rows a correction claim could be about: rows naming the claim's subject (or the person it
    resolves to) as whole words, newest first, at most ``MAX_ROWS_READ``. Selection only, never authority."""
    names = set()
    for c in claims:
        if not isinstance(c, mc.CorrectionClaim):
            continue
        form = mc.subject_form(c.subject)
        names.add(mc.label_key(form.value if form.form == "name" else c.subject))
        try:
            res = graph.resolve(form.value) if form.form == "name" else None
        except Exception:  # noqa: BLE001 - the validator reports the failed read (rule 5)
            res = None
        names.update(mc.label_key(m.name) for m in (res.matches if res else ()))
    names.discard("")
    hits = [r for r in refs if any(mc.occurs_in(r.text or "", n) for n in names)]
    hits.sort(key=lambda r: -float((r.metadata or {}).get("added_ts") or 0))
    return hits[:MAX_ROWS_READ]


def read_attrs(raw: str, texts: Sequence[str], user_id: str, graph: vm.Graph, at: Any) -> dict:
    """The blind attribute read, kept only where code can check it: the span is a verbatim, single occurrence in the row,
    the value normalises under the attribute, and the subject resolves from the ROW's own words. ``{row index: (Attr, ..)}``."""
    out: dict = {}
    try:
        items = json.loads(raw).get("rows") or []
    except Exception:  # noqa: BLE001
        return out
    for it in items if isinstance(items, list) else []:
        try:
            i, attribute, span = int(it["row"]), str(it["attribute"]), str(it["span"])
            text = texts[i]
            start = text.find(span)
            if i < 0 or start < 0 or text.find(span, start + 1) != -1 or attribute not in mc.ATTRIBUTES:
                continue
            value = mc.normalise(attribute, span)
            if value is None:
                continue
            ctx = vm.Context(user_id, [vm.Turn(0, at, text)], [], graph)
            ref = vm.resolve_subject(str(it["subject"]), 0, ctx, allow_label=True)
            out.setdefault(i, []).append(vm.Attr(ref, attribute, value.value, value.unit, f"{start}:{start + len(span)}"))
        except Exception:  # noqa: BLE001 - one unreadable item is dropped, never guessed
            continue
    return {i: tuple(a) for i, a in out.items()}


def _budget_ok(now: float) -> bool:
    while _calls and now - _calls[0] > 3600:
        _calls.popleft()
    return now >= _open_until and len(_calls) < MAX_PER_HOUR


async def _call(make) -> Optional[str]:
    """One budgeted, scheduler-admitted model call; ``None`` = returned nothing (budget, breaker, scheduler, failure)."""
    global _fail, _open_until
    import brain_scheduler

    now = time.monotonic()
    if not _budget_ok(now):
        STATS["skipped_budget"] += 1
        return None
    if brain_scheduler.mode() != "off" and brain_scheduler.decide(LABEL).decision != "allowed":
        # stricter than the scheduler's own "log" mode: a call it would only COUNT as an idle 4B call is not made at all
        STATS["skipped_scheduler"] += 1
        return None
    _calls.append(now)
    STATS["calls"] += 1
    try:
        raw = await asyncio.wait_for(make(), timeout=TIMEOUT_S + 600.0)     # the slot gate may hold it behind a conversation
        _fail = 0
        return raw
    except Exception as exc:  # noqa: BLE001
        _fail += 1
        STATS["error"] += 1
        if _fail >= BREAKER_AFTER:
            _open_until, _fail = time.monotonic() + BREAKER_S, 0
            logger.warning("MEMORY_CLAIM_PASS breaker open for %ds (%s)", int(BREAKER_S), type(exc).__name__)
        return None


async def _forgotten_lookup(user_id: str, claims: Sequence[Any], walls: Any):
    known: dict = {}
    for c in claims:
        for text in (getattr(c, "subject", ""), getattr(c, "quote", "")):
            if text and text not in known:
                try:
                    known[text] = bool(await walls.forgotten(user_id, text))
                except Exception:  # noqa: BLE001 - unreadable: the validator's rule 18 treats a raise as forgotten
                    known[text] = None

    def forgotten(text: str) -> bool:
        if known.get(text) is None:
            raise LookupError("forget ledger not read for this text")
        return known[text]

    return forgotten


async def process_session(s: Session, src: Any, reader: Any, *, walls: Any = None, mode_: Optional[str] = None,
                          **apply_kw: Any) -> dict:
    """Read, validate and (per the flag) apply the claims of one session. ``apply_kw`` (svc, registry, fix_date, classes)
    goes to ``memory_claims_apply.apply`` unchanged. Returns counts and the verdicts as (verdict, rule). Never raises."""
    apply_kw["walls"] = walls if walls is not None else ap.Walls()
    import medical_safety
    import turn_forget
    import user_erase_gate
    import user_prefs

    m = ap.mode() if mode_ is None else mode_
    walls = apply_kw["walls"]
    t0 = time.monotonic()
    out = {"flagged": len(s.flagged), "claims": 0, "written": 0}
    try:
        raw = await _call(lambda: reader.claims(s.turns, s.flagged))
        if raw is None:
            return {**out, "skipped": "no_read"}             # returned nothing: the turns stay unhandled and are retried
        parsed = mc.parse_output(raw)
        claims = [c for c in parsed.items if not isinstance(c, mc.Malformed)]
        graph, refs = await src.graph(s.user_id), []
        if any(isinstance(c, mc.CorrectionClaim) for c in claims):
            refs = candidate_rows(await src.rows(s.user_id), claims, graph)
        attrs = {i: ap.attrs_of(r.metadata) for i, r in enumerate(refs)}
        blind = [i for i, a in attrs.items() if not a]
        if blind:
            raw_rows = await _call(lambda: reader.attributes([refs[i].text or "" for i in blind]))
            got = read_attrs(raw_rows or "", [refs[i].text or "" for i in blind], s.user_id, graph, s.turns[-1].at)
            attrs.update({blind[k]: v for k, v in got.items()})
        rows = [vm.Row(r.id, r.text or "", mc.APPROVED, (r.metadata or {}).get("entity_id"), attrs.get(i, ()))
                for i, r in enumerate(refs)]
        try:
            opted_out = bool(await user_prefs.is_memory_opted_out(s.user_id))
        except Exception:  # noqa: BLE001 - as value_correction.apply: review(edit) keeps its own opt-out wall behind this
            opted_out = False
        ctx = vm.Context(
            s.user_id, s.turns, rows, graph, opted_out=opted_out,
            retracted=any(turn_forget.blocks_write(s.user_id, t.text) for t in s.turns),
            forgotten=await _forgotten_lookup(s.user_id, claims, walls), erase_generation_scheduled=s.generation,
            erase_generation_now=user_erase_gate.generation(s.user_id), detector=medical_safety.detect)
        verdicts = vm.validate(parsed, ctx)
        vm.record(verdicts, LANE)
        job = ap.Job(s.user_id, s.turns, rows, s.generation, s.session_id,
                     people={p.id: (p.name, p.relationship) for p in getattr(graph, "_people", ())})
        per_turn: dict = {t: {} for t in s.flagged}
        seen: dict = {}
        alias: dict = {}             # the validator's planned row id -> the id the writer really gave the replacement
        out["verdicts"] = [(v.verdict, v.rule) for v in verdicts]
        pairs = [(c, v) for c, v in zip(parsed.items, verdicts) if not isinstance(c, mc.Malformed)]
        for claim, verdict in sorted(pairs, key=lambda cv: cv[0].turn):     # oldest first, as the validator decided them
            out["claims"] += 1
            verdict = replace(verdict, edits=tuple(replace(e, row_id=alias.get(e.row_id, e.row_id)) for e in verdict.edits))
            res = await ap.apply(verdict, claim, job, mode_=m, **apply_kw)
            out["written"] += res.rows if res.code == ap.C_WRITTEN else 0
            if res.code == ap.C_WRITTEN and res.plans:
                # a later claim of the chain sees the row as it now is (in shadow nothing was written, so a second step of
                # a chain inside one session is not planned: it reports stale_row)
                alias.update({mc.planned_row_id(p.row_id, verdict.claim_id): nid for p, nid in zip(res.plans, res.new_ids)})
                job = replace(job, rows=_after(job.rows, res))
            per_turn.setdefault(claim.turn, {}).update({p.row_id: p.new_text for p in res.plans} if res.writes else {})
            seen[claim.turn] = (claim.type, verdict.rule)
        ms = int((time.monotonic() - t0) * 1000)
        by_t = {t.t: t for t in s.turns}
        for t in s.flagged:
            old = ap.old_path_plan(by_t[t].text, rows)
            ctype, rule = seen.get(t, (mc.NONE, None))
            ap.log_shadow(ctype, LANE, ap.compare(old, per_turn.get(t) or {}), rule, len(old), len(per_turn.get(t) or {}), ms,
                          MODEL)
            _mark_handled(s.user_id, by_t[t].turn_id)
            if m == ap.ON and per_turn.get(t):
                src.clear_marker(s.user_id, s.message_ids.get(t, ""))
        return out
    except Exception as exc:  # noqa: BLE001 - a failed read of the graph, the rows or the ledger skips the session
        STATS["session_error"] += 1
        logger.warning("MEMORY_CLAIM_PASS session skipped (%s)", type(exc).__name__)
        return {**out, "skipped": "error"}


def _after(rows: Sequence[vm.Row], res: ap.Outcome) -> tuple:
    new = {p.row_id: (nid, p) for p, nid in zip(res.plans, res.new_ids)}
    return tuple(vm.Row(new[r.id][0], new[r.id][1].new_text, mc.APPROVED, r.entity_id, new[r.id][1].attrs) if r.id in new else r
                 for r in rows)


def _mark_handled(user_id: str, turn_id: str) -> None:
    _handled[f"{user_id}|{turn_id}"] = time.time()
    while len(_handled) > HANDLED_MAX:
        _handled.popitem(last=False)


async def run_sweep(*, src: Any = None, reader: Any = None, **kw: Any) -> dict:
    """One bounded pass over the idle sessions. ``{"mode": "off"}`` and no work at all when the flag is off."""
    m = kw.get("mode_") or ap.mode()
    if m not in (ap.SHADOW, ap.ON):
        return {"mode": ap.OFF}
    src = src if src is not None else Source()
    reader = reader if reader is not None else BrainReader()
    t0, calls0 = time.monotonic(), STATS["calls"]
    total = {"mode": m, "sessions": 0, "flagged": 0, "claims": 0, "written": 0}
    skipped: collections.Counter = collections.Counter()
    budget = MAX_TURNS_PER_PASS
    try:
        sessions = await src.sessions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MEMORY_CLAIM_PASS sessions not listed (%s)", type(exc).__name__)
        return {**total, "skipped": "error"}
    for row in sessions[:4 * MAX_SESSIONS]:
        if budget <= 0 or total["sessions"] >= MAX_SESSIONS or time.monotonic() - t0 > TIME_BUDGET_S:
            skipped["budget"] += 1
            break
        try:
            s = await load_session(src, row["session_id"], row.get("user_id"), budget)
        except Exception as exc:  # noqa: BLE001 - includes an unreadable forget ledger: retried on a later sweep
            skipped[type(exc).__name__] += 1
            continue
        if s is None:
            continue
        res = await process_session(s, src, reader, **kw)
        total["sessions"] += 1
        budget -= len(s.flagged)
        for k in ("flagged", "claims", "written"):
            total[k] += res.get(k, 0)
        if res.get("skipped"):
            skipped[res["skipped"]] += 1
    if total["sessions"] or skipped:
        logger.info(LOG_PASS, m, total["sessions"], total["flagged"], STATS["calls"] - calls0, total["claims"],
                    ",".join(f"{k}:{v}" for k, v in sorted(skipped.items())) or "-", int((time.monotonic() - t0) * 1000))
    return total
