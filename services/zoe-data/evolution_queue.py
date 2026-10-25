"""The single evolution proposal queue (self-evolution Phase 0; design ``docs/architecture/self-evolution-nightly-loop.md`` section 2).

ONE table, ``evolution_proposals`` (migration 0004, the autonomy columns of 0027, the queue columns of 0062), holds every proposed
change to Zoe, whoever proposed it:

  * ``change_class`` - ``harness`` (overlay text: prompts, doctrines, manner, tool guidance, recall shape; a router example),
    ``release`` (a program update: a signed release, applied by the caretaker) or ``code`` (the maintainer's PR process only).
  * ``risk_tier`` - 0 harness text off the voice path, 1 harness on the voice path (the 04:30 replay gate must pass), 2 a release
    (backup first, rollback kept), 3 code (never applied by the loop; a maintainer's merged PR is what "applied" means).
  * ``component`` - what it changes (``router``, ``intent_router``, ``brain``, ``stt``, ``manner``...), a short tag.
  * ``status`` - ``proposed`` -> ``probed`` -> ``applied`` -> ``canary`` -> ``kept`` or ``reverted``; or ``rejected``. The legal moves
    are ``TRANSITIONS`` and ONLY this module writes a status: ``transition`` refuses an illegal move (``IllegalTransition``) and a move
    from a status the row no longer has (``Conflict``, compare-and-swap). Every move is an ``evolution_proposal_history`` row.
  * ``decision`` - a person's verdict on an OPEN proposal (empty or ``approved``), separate from the status: approving a code proposal
    sends it to the maintainer's lane, it does not apply anything. The loop itself never asks anyone (owner, 2026-10-19).
  * ``evidence_json`` - the gate's evidence as JSON (``EVIDENCE_KEYS``: the signal's counts, scores ``before`` / ``after``, the control
    arms, the ``diff``...). The legacy ``evidence`` TEXT column stays the writer's own signal record for the legacy readers.
  * ``lesson_id`` - the lesson this attempt produced (the per-domain lesson library, a later phase).
  * ``source`` - which writer: ``struggle`` (``struggle_proposals.py``; its domain detail stays in ``struggle_proposals``, keyed by the
    SAME id), ``evolution_notice:<kind>``, ``mcp:create_evolution_proposal``, ``legacy`` (rows from before 0062).

Writers: ``propose``, ``transition``, ``decide``, ``attach_evidence``, ``update_text``, ``remove``. Every one takes the caller's ``db``
(``db_pool.get_db_ctx()`` or a test double) and uses ``?`` placeholders with ONE params tuple, which both the asyncpg compat layer and
SQLite accept. Readers: ``list_queue``, ``get``, ``history``, ``summary`` - the one read API the Zoe score page will use
(``GET /api/system/evolution/queue`` in ``routers/system.py``).

Pinned by ``tests/test_evolution_queue.py`` (transitions with negative controls, the migration's remap, the one-writer rule).
"""
from __future__ import annotations

import contextlib
import json
import time
import uuid
from typing import Any, Iterable, Optional

CLASSES = ("harness", "release", "code")
STATUSES = ("proposed", "probed", "applied", "canary", "kept", "reverted", "rejected")
#: statuses a person may still decide on (and a proposal may still be rejected from)
OPEN = ("proposed", "probed")
#: the change is live (or was)
LANDED = ("applied", "canary", "kept")
#: the only legal moves. ``reverted`` / ``rejected`` -> ``proposed`` is a REOPEN (the same problem recurred after the verdict); everything
#: else is forwards. No move skips the gate's order: nothing goes canary -> applied or kept -> canary, and nothing leaves ``proposed``
#: straight for ``kept``.
TRANSITIONS: dict = {
    "proposed": frozenset({"probed", "applied", "rejected"}),
    "probed": frozenset({"applied", "rejected"}),
    "applied": frozenset({"canary", "kept", "reverted"}),
    "canary": frozenset({"kept", "reverted"}),
    "kept": frozenset({"reverted"}),
    "reverted": frozenset({"proposed"}),
    "rejected": frozenset({"proposed"}),
}
DECISIONS = ("", "approved")
RISK_TIERS = (0, 1, 2, 3)
DEFAULT_RISK_TIER = {"harness": 1, "release": 2, "code": 3}
EVIDENCE_KEYS = ("signal", "before", "after", "controls", "diff", "probes", "gate", "canary", "ref")
DIFF_MAX = 65536
EVIDENCE_MAX = 262144
TAG_MAX = 64
TITLE_MAX = 300

#: the pre-0062 status names, for the legacy API and its readers: legacy name -> (the statuses it covers, decision or None for any).
#: The exact inverse of ``legacy_status`` (a test pins it): pending / approved are every OPEN row, deployed is applied or canary.
LEGACY_STATUS = {
    "pending": (OPEN, ""),
    "approved": (OPEN, "approved"),
    "deployed": (("applied", "canary"), None),
    "validated": (("kept",), None),
    "failed": (("reverted",), None),
    "rejected": (("rejected",), None),
}

#: a legacy proposal type -> the component it changes (migration 0062 backfills with the same map; tests/test_evolution_queue.py pins it)
LEGACY_COMPONENT = {
    "intent_pattern": "intent_router",
    "user_frustration": "chat",
    "user_issue_report": "chat",
    "agent_health": "brain",
    "security_vulnerability": "security",
    "security_improvement": "security",
}


def component_for_type(proposal_type: Any) -> str:
    """The queue component of a legacy proposal type (the type itself when it has no mapping)."""
    t = str(proposal_type or "").strip()
    return LEGACY_COMPONENT.get(t) or t[:TAG_MAX] or "code"


_COLS = ("id", "type", "title", "description", "status", "decision", "change_class", "risk_tier", "component", "evidence_json",
         "lesson_id", "source", "decided_by", "decision_note", "proposed_at", "updated_at", "reviewed_at", "deployed_at",
         "multica_issue_id")


class QueueError(Exception):
    """Base of the queue's refusals."""


class IllegalTransition(QueueError):
    """The move is not in ``TRANSITIONS`` (or the value is not a status at all)."""


class NotFound(QueueError):
    pass


class Conflict(QueueError):
    """The row moved under the caller (compare-and-swap lost), or a decision on a proposal that is no longer open."""


def legal(frm: Optional[str], to: str) -> bool:
    return frm in TRANSITIONS and to in TRANSITIONS[frm]


def check_transition(frm: Optional[str], to: str) -> None:
    if to not in STATUSES:
        raise IllegalTransition(f"{to!r} is not a status ({', '.join(STATUSES)})")
    if frm not in STATUSES:
        raise IllegalTransition(f"{frm!r} is not a status")
    if not legal(frm, to):
        allowed = ", ".join(sorted(TRANSITIONS[frm])) or "nothing"
        raise IllegalTransition(f"{frm} -> {to} is not allowed (from {frm}: {allowed})")


def legacy_status(status: str, decision: str = "") -> str:
    """The pre-0062 name of (status, decision), for the legacy listing and its readers."""
    if status in OPEN:
        return "approved" if decision == "approved" else "pending"
    return {"applied": "deployed", "canary": "deployed", "kept": "validated", "reverted": "failed"}.get(status, status)


def _tag(value: Any, what: str) -> str:
    s = str(value or "").strip()
    if not s or len(s) > TAG_MAX:
        raise ValueError(f"{what} must be 1-{TAG_MAX} characters")
    return s


def normalize_evidence(evidence: Optional[dict]) -> dict:
    """Validate the gate evidence: a dict of ``EVIDENCE_KEYS`` only, ``before`` / ``after`` / ``controls`` dicts, ``diff`` a string of at
    most ``DIFF_MAX`` characters, the whole under ``EVIDENCE_MAX`` serialised. Raises ``ValueError``."""
    if evidence is None:
        return {}
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    unknown = sorted(set(evidence) - set(EVIDENCE_KEYS))
    if unknown:
        raise ValueError(f"unknown evidence keys: {', '.join(unknown)}")
    for key in ("before", "after", "controls"):
        if key in evidence and not isinstance(evidence[key], dict):
            raise ValueError(f"evidence.{key} must be an object")
    if "diff" in evidence and (not isinstance(evidence["diff"], str) or len(evidence["diff"]) > DIFF_MAX):
        raise ValueError(f"evidence.diff must be a string of at most {DIFF_MAX} characters")
    out = json.loads(json.dumps(evidence, sort_keys=True, default=str))
    if len(json.dumps(out)) > EVIDENCE_MAX:
        raise ValueError("evidence is too large")
    return out


def _json(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def _rowcount(cur: Any) -> int:
    try:
        return int(getattr(cur, "rowcount", 0) or 0)
    except (TypeError, ValueError):
        return 0


async def _rows(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, tuple(params))
    return list(await cur.fetchall()) if cur is not None else []


def _vals(r: Any, n: int) -> tuple:
    """The first ``n`` values of a row, whatever the driver hands back (tuple, sqlite3.Row, asyncpg Record, dict)."""
    if isinstance(r, dict):
        return tuple(r.values())[:n]
    return tuple(r[i] for i in range(n))


def _row(r: Any) -> dict:
    return dict(zip(_COLS, _vals(r, len(_COLS))))


async def _history(db, pid: str, frm: Optional[str], to: Optional[str], decision: str, actor: str, note: str, now: float) -> None:
    await db.execute(
        "INSERT INTO evolution_proposal_history (id, proposal_id, at, from_status, to_status, decision, actor, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, pid, now, frm, to, decision, str(actor or "")[:80], str(note or "")[:200]))


def _atomic(db):
    """ONE transaction for a write and its history row (asyncpg: ``conn.transaction()``, a savepoint inside an open one), so a move
    never lands without its history. A test double without transactions runs the statements as they come."""
    tx = getattr(db, "transaction", None)
    return tx() if callable(tx) else contextlib.nullcontext()


atomic = _atomic        # public: a caller that pairs a queue move with its OWN write wraps both in this one transaction


async def _fetch(db, pid: str) -> dict:
    rows = await _rows(db, f"SELECT {', '.join(_COLS)} FROM evolution_proposals WHERE id = ?", (pid,))
    if not rows:
        raise NotFound(pid)
    return _row(rows[0])


# -- writers ------------------------------------------------------------------

async def propose(db, *, source: str, change_class: str, component: str, title: str, description: str = "",
                  risk_tier: Optional[int] = None, evidence: Optional[dict] = None, proposal_id: Optional[str] = None,
                  status: str = "proposed", legacy_type: Optional[str] = None, legacy_evidence: Optional[str] = None,
                  target_patterns: Optional[str] = None, actor: str = "", now: Optional[float] = None) -> str:
    """Add one proposal to the queue. It enters OPEN: ``proposed``, or ``probed`` when the writer already holds the probe evidence.
    Returns the id. The legacy ``type`` column is ``legacy_type`` or ``<class>:<component>``."""
    if change_class not in CLASSES:
        raise ValueError(f"change_class must be one of {', '.join(CLASSES)}")
    if status not in OPEN:
        raise IllegalTransition(f"a proposal enters the queue as proposed or probed, not {status}")
    tier = DEFAULT_RISK_TIER[change_class] if risk_tier is None else risk_tier
    if tier not in RISK_TIERS:
        raise ValueError(f"risk_tier must be one of {RISK_TIERS}")
    if change_class == "code" and tier != 3:
        raise ValueError("a code change is risk tier 3: only the maintainer PR process applies it")
    src = _tag(source, "source")
    comp = _tag(component, "component")
    head = str(title or "").strip()[:TITLE_MAX]
    if not head:
        raise ValueError("title is required")
    ev = normalize_evidence(evidence)
    pid = proposal_id or uuid.uuid4().hex
    t = time.time() if now is None else float(now)
    async with _atomic(db):
        await db.execute(
            "INSERT INTO evolution_proposals (id, type, title, description, evidence, target_patterns, status, proposed_at, change_class, "
            "risk_tier, component, evidence_json, lesson_id, source, decision, decided_by, decision_note, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, legacy_type or f"{change_class}:{comp}", head, str(description or ""), legacy_evidence, target_patterns, status, t,
             change_class, tier, comp, json.dumps(ev, sort_keys=True), None, src, "", "", "", t))
        await _history(db, pid, None, status, "", actor or src, "proposed", t)
    return pid


async def transition(db, pid: str, to: str, *, actor: str, frm: Optional[str] = None, note: str = "",
                     evidence: Optional[dict] = None, require_decision: Optional[str] = None, lesson_id: Optional[str] = None,
                     now: Optional[float] = None) -> str:
    """Move ``pid`` to ``to``. ``frm`` (when the caller already knows it) is the status the move is FROM; otherwise the row is read.
    Refuses an illegal move (``IllegalTransition``); refuses when the row is no longer in ``frm`` or, with ``require_decision``, does not
    carry that decision (``Conflict``). ``evidence`` merges into ``evidence_json`` key by key. Returns the status it moved from."""
    t = time.time() if now is None else float(now)
    row = None
    if frm is None or evidence is not None:
        row = await _fetch(db, pid)
        if frm is None:
            frm = str(row["status"])
        elif str(row["status"]) != frm:
            raise Conflict("proposal is " + str(row["status"]) + ", not " + str(frm))
    check_transition(frm, to)
    sets = ["status = ?", "updated_at = ?"]
    params: list = [to, t]
    if to == "applied":
        sets += ["deployed_at = ?", "validation_result = NULL"]     # every deploy is measured afresh (a retry keeps no old verdict)
        params.append(t)
    if to == "rejected":
        sets += ["reviewed_at = ?", "decided_by = ?"]
        params += [t, str(actor or "")[:80]]
    if to == "proposed":                 # a reopen starts clean: the old verdict (and its note) does not carry over
        sets += ["decision = ?", "decided_by = ?", "reviewed_at = NULL", "decision_note = ?"]
        params += ["", "", str(note or "")[:200]]
    elif note:
        sets.append("decision_note = ?")
        params.append(str(note)[:200])
    if lesson_id is not None:
        sets.append("lesson_id = ?")
        params.append(_tag(lesson_id, "lesson_id"))
    if evidence is not None:
        merged = dict(_json(row["evidence_json"]) if row else {})
        merged.update(normalize_evidence(evidence))
        sets.append("evidence_json = ?")
        params.append(json.dumps(normalize_evidence(merged), sort_keys=True))
    where = "id = ? AND status = ?"
    params += [pid, frm]
    if require_decision is not None:
        where += " AND decision = ?"
        params.append(require_decision)
    async with _atomic(db):
        cur = await db.execute("UPDATE evolution_proposals SET " + ", ".join(sets) + " WHERE " + where, tuple(params))
        if _rowcount(cur) <= 0:
            if row is None:
                await _fetch(db, pid)        # NotFound beats Conflict
            need = (" with decision " + repr(require_decision)) if require_decision is not None else ""
            raise Conflict("proposal " + pid + " is no longer " + str(frm) + need)
        await _history(db, pid, frm, to, "", actor, note, t)
    return frm


async def decide(db, pid: str, decision: str, *, actor: str, note: str = "", guard: Optional[dict] = None,
                 now: Optional[float] = None) -> None:
    """Record a person verdict on an OPEN proposal (``approved``, or empty to take it back). Changes no status: approving is not
    applying. ``guard`` maps column -> value that must still hold (compare-and-swap, e.g. the autonomy contract the approving gate
    read). ``Conflict`` when the proposal is not open or the guard moved."""
    if decision not in DECISIONS:
        raise ValueError("decision must be one of " + repr(DECISIONS))
    t = time.time() if now is None else float(now)
    sets = "decision = ?, decided_by = ?, reviewed_at = ?, updated_at = ?" + (", decision_note = ?" if note else "")
    params: list = [decision, str(actor or "")[:80], t, t] + ([str(note)[:200]] if note else [])
    where = "id = ? AND status IN (" + ", ".join("?" for _ in OPEN) + ")"
    params += [pid, *OPEN]
    for col, val in (guard or {}).items():
        if col not in ("autonomy_class", "approval_required", "decision"):
            raise ValueError("cannot guard on " + str(col))
        where += " AND (" + col + " = ? OR (" + col + " IS NULL AND CAST(? AS TEXT) IS NULL))"
        params += [val, val]
    async with _atomic(db):
        cur = await db.execute("UPDATE evolution_proposals SET " + sets + " WHERE " + where, tuple(params))
        if _rowcount(cur) <= 0:
            row = await _fetch(db, pid)
            if row["status"] not in OPEN:
                raise Conflict("proposal is " + str(row["status"]))
            raise Conflict("the proposal changed under the decision")
        await _history(db, pid, None, None, decision or "withdrawn", actor, note, t)


async def attach_evidence(db, pid: str, evidence: dict, *, now: Optional[float] = None) -> dict:
    """Merge ``evidence`` into the row ``evidence_json`` (key by key). Returns the merged evidence. Never a status."""
    row = await _fetch(db, pid)
    merged = dict(_json(row["evidence_json"]))
    merged.update(normalize_evidence(evidence))
    merged = normalize_evidence(merged)
    t = time.time() if now is None else float(now)
    await db.execute("UPDATE evolution_proposals SET evidence_json = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(merged, sort_keys=True), t, pid))
    return merged


async def update_text(db, pid: str, *, title: Optional[str] = None, description: Optional[str] = None,
                      now: Optional[float] = None) -> None:
    """The writer changed its own sentence (a recount). Never a status."""
    sets: list = []
    params: list = []
    if title is not None:
        sets.append("title = ?")
        params.append(str(title).strip()[:TITLE_MAX] or "(untitled)")
    if description is not None:
        sets.append("description = ?")
        params.append(str(description))
    if not sets:
        return
    sets.append("updated_at = ?")
    params += [time.time() if now is None else float(now), pid]
    await db.execute("UPDATE evolution_proposals SET " + ", ".join(sets) + " WHERE id = ?", tuple(params))


async def remove(db, pids: Iterable[str], *, source: Optional[str] = None) -> None:
    """Remove proposals and their history (a writer withdrawing its own row, or the forget cascade). ``source`` restricts the delete
    to that writer rows, so one writer can never remove another writer row."""
    for pid in sorted(set(str(p) for p in pids if p)):
        async with _atomic(db):
            if source is not None:
                await db.execute("DELETE FROM evolution_proposal_history WHERE proposal_id IN "
                                 "(SELECT id FROM evolution_proposals WHERE id = ? AND source = ?)", (pid, source))
                await db.execute("DELETE FROM evolution_proposals WHERE id = ? AND source = ?", (pid, source))
            else:
                await db.execute("DELETE FROM evolution_proposal_history WHERE proposal_id = ?", (pid,))
                await db.execute("DELETE FROM evolution_proposals WHERE id = ?", (pid,))


# -- readers (the Zoe score page) ---------------------------------------------

def public(row: dict) -> dict:
    """The read API view of a row: the queue fields only (the legacy free-text ``evidence`` and ``description`` are not served)."""
    return {
        "id": row["id"], "class": row["change_class"], "risk_tier": row["risk_tier"], "component": row["component"],
        "status": row["status"], "decision": row["decision"] or "", "title": row["title"], "source": row["source"],
        "evidence": _json(row["evidence_json"]), "lesson_id": row["lesson_id"], "proposed_at": row["proposed_at"],
        "updated_at": row["updated_at"], "decided_at": row["reviewed_at"], "decided_by": row["decided_by"] or "",
        "applied_at": row["deployed_at"],
    }


def _ctx():
    from db_pool import get_db_ctx  # type: ignore[import]

    return get_db_ctx()


async def list_queue(*, status: Optional[str] = None, change_class: Optional[str] = None, component: Optional[str] = None,
                     source: Optional[str] = None, limit: int = 50) -> list:
    """Newest first. Filters are exact values; an unknown status / class is refused (``ValueError``) rather than matching nothing."""
    if status is not None and status not in STATUSES:
        raise ValueError("status must be one of " + ", ".join(STATUSES))
    if change_class is not None and change_class not in CLASSES:
        raise ValueError("class must be one of " + ", ".join(CLASSES))
    sql = "SELECT " + ", ".join(_COLS) + " FROM evolution_proposals WHERE 1 = 1"
    params: list = []
    for col, val in (("status", status), ("change_class", change_class), ("component", component), ("source", source)):
        if val is not None:
            sql += " AND " + col + " = ?"
            params.append(val)
    sql += " ORDER BY COALESCE(updated_at, proposed_at) DESC LIMIT ?"
    params.append(int(max(1, min(500, limit))))
    async with _ctx() as db:
        return [public(_row(r)) for r in await _rows(db, sql, tuple(params))]


async def get(pid: str) -> dict:
    async with _ctx() as db:
        return public(await _fetch(db, pid))


async def history(pid: str) -> list:
    async with _ctx() as db:
        rows = await _rows(db, "SELECT at, from_status, to_status, decision, actor, note FROM evolution_proposal_history "
                               "WHERE proposal_id = ? ORDER BY at", (pid,))
    return [dict(zip(("at", "from", "to", "decision", "actor", "note"), _vals(r, 6))) for r in rows]


async def summary(*, since_s: float = 30 * 86400.0, now: Optional[float] = None) -> dict:
    """Counts for the score page: ``by_class`` (class -> status -> n), ``open``, the kept / reverted moves in the last ``since_s``, and
    the oldest open proposal age in days. Counts only."""
    t = time.time() if now is None else float(now)
    marks = ", ".join("?" for _ in OPEN)
    async with _ctx() as db:
        rows = await _rows(db, "SELECT change_class, status, COUNT(*) FROM evolution_proposals GROUP BY change_class, status")
        moves = await _rows(db, "SELECT to_status, COUNT(*) FROM evolution_proposal_history WHERE at >= ? AND to_status IN (?, ?) "
                                "GROUP BY to_status", (t - since_s, "kept", "reverted"))
        oldest = await _rows(db, "SELECT MIN(proposed_at) FROM evolution_proposals WHERE status IN (" + marks + ")", OPEN)
    by_class = dict((c, dict((s, 0) for s in STATUSES)) for c in CLASSES)
    for r in rows:
        cls, st, n = _vals(r, 3)
        if cls in by_class and st in by_class[cls]:
            by_class[cls][st] = int(n)
    recent = {"kept": 0, "reverted": 0}
    for r in moves:
        st, n = _vals(r, 2)
        recent[str(st)] = int(n)
    first = _vals(oldest[0], 1)[0] if oldest else None
    return {
        "by_class": by_class,
        "open": sum(by_class[c][s] for c in CLASSES for s in OPEN),
        "total": sum(sum(v.values()) for v in by_class.values()),
        "recent_days": round(since_s / 86400.0, 1), "recent": recent,
        "oldest_open_age_days": round((t - float(first)) / 86400.0, 1) if first else None,
    }
