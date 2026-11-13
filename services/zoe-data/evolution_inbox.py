"""The owner's DECIDE surface over the evolution queue (read model + verdicts; ``routers/evolution_inbox.py``, page ``evolution.html``).

The queue held 777 open proposals and no screen anyone could use, so nothing was ever decided. This module turns it into a SHORT list:
near-identical proposals (same component, class, kind and normalised title or lesson) are ONE card with a count, ranked by evidence
and recency, at most ``CARD_CAP``; anything with no activity for ``stale_days`` is only a count ("N old proposals hidden") with a bulk
dismiss. Struggle proposals and the owner's own failed-turn clusters, which the legacy list leaves out, are cards too.

CONTENT WALL: a legacy / NOTICE title, description or free-text evidence can hold a member's words ("Intent gap: '<what they said>'"),
so none of them is ever returned. A card says what Zoe noticed from the KIND and the counts, the struggle sentence (text-free by
construction, ``struggle_proposals.valid_summary``) and dates. Counts and dates only.

A verdict goes through ``evolution_queue`` (approve = the ``decision`` column, reject = the ``rejected`` status, later = the legacy
defer: ``next_review_at`` + the decision withdrawn) or, for a struggle card, ``struggle_proposals.decide``. Each is idempotent (a
repeat changes nothing and writes no second history row) and the queue history is the audit trail (who, when, the reason CODE).
Approving applies NOTHING: it records the decision; acting on it is a later lane.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from typing import Any, Optional

import evolution_queue as eq

logger = logging.getLogger("evolution_inbox")

CARD_CAP = 20
STALE_DAYS = 30.0
LATER_DAYS = 7.0
SCAN_LIMIT = 5000
MAX_IDS = 500
#: decision reasons are CODES (a free-text note could carry a member's words); the same set as ``struggle_proposals.REASONS``
REASONS = ("", "not_a_problem", "wrong_label", "already_fixed", "not_now", "other")
#: the only evidence numbers a card sums (``evidence_json.signal``; counts written by the queue writers)
_COUNT_KEYS = ("miss_count", "repeat_count", "errors")
_STOP = frozenset("a an the to of in on for and or is it my me you your zoe please can could would what how".split())

_NOTICED = {
    "intent_miss_cluster": "Requests Zoe did not understand (similar wording, grouped)",
    "intent_pattern": "Requests Zoe did not understand (similar wording, grouped)",
    "user_frustration": "Someone asked for the same thing again and again without a good answer",
    "user_issue_report": "Someone told Zoe something was not working",
    "agent_health": "A model tier was slow or failing often",
}
_APPROVAL_TODAY = ("Today a yes only records your decision. Nothing is changed, built or retrained automatically: acting on an "
                   "approved proposal is not built yet.")


def _norm(title: str) -> str:
    words = sorted({w for w in re.findall(r"[a-z]+", str(title or "").lower()) if w not in _STOP and len(w) > 1})
    return " ".join(words)


def _date(ts: Any) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts))) if ts else None
    except (TypeError, ValueError, OverflowError):
        return None


def _kind(row: dict) -> str:
    sig = eq._json(row.get("evidence_json")).get("signal")
    k = sig.get("kind") if isinstance(sig, dict) else None
    return str(k or row.get("type") or "unknown")[:40]


def _strength(row: dict) -> int:
    sig = eq._json(row.get("evidence_json")).get("signal")
    if not isinstance(sig, dict):
        return 0
    return sum(int(v) for k, v in sig.items() if k in _COUNT_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool))


def _proposes(change_class: str, component: str, tier: Any) -> str:
    if change_class == "code":
        return f"A code change to {component}, made by the maintainer through a reviewed pull request (risk tier {tier})."
    if change_class == "release":
        return f"A program update for {component} (risk tier {tier})."
    return f"A change to how Zoe handles {component} (risk tier {tier})."


def _score(count: int, strength: int, last: float, now: float) -> float:
    age_days = max(0.0, (now - last) / 86400.0)
    return round((count + strength) * math.pow(0.5, age_days / 14.0), 4)


_QCOLS = ("id", "type", "title", "decision", "change_class", "risk_tier", "component", "evidence_json", "lesson_id", "proposed_at",
          "updated_at", "next_review_at")


async def _queue_rows(db) -> list[dict]:
    rows = await eq._rows(db, "SELECT " + ", ".join(_QCOLS) + " FROM evolution_proposals WHERE status IN (?, ?) "
                              "AND COALESCE(source, '') <> 'struggle' LIMIT ?", (*eq.OPEN, SCAN_LIMIT))
    return [dict(zip(_QCOLS, eq._vals(r, len(_QCOLS)))) for r in rows]


def _last(row: dict) -> float:
    return float(row.get("updated_at") or row.get("proposed_at") or 0)


def _queue_cards(rows: list[dict], now: float) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["component"], r["change_class"], _kind(r), r.get("lesson_id") or _norm(r["title"]))
        groups.setdefault(key, []).append(r)
    cards = []
    for (component, cls, kind, _k), members in groups.items():
        first = min(float(m.get("proposed_at") or 0) for m in members)
        last = max(_last(m) for m in members)
        strength = sum(_strength(m) for m in members)
        tier = max(int(m.get("risk_tier") or 0) for m in members)
        ids = sorted(m["id"] for m in members)
        cards.append({
            "card": "q-" + hashlib.sha1("|".join(ids).encode()).hexdigest()[:12], "source": "queue", "ids": ids, "count": len(ids),
            "kind": kind, "component": component, "change_class": cls, "risk_tier": tier,
            "noticed": _NOTICED.get(kind, f"Zoe noticed a recurring problem with {component}"),
            "evidence": {"proposals": len(ids), "signal_count": strength},
            "first_seen": _date(first), "last_seen": _date(last), "proposes": _proposes(cls, component, tier),
            "approved": all(m.get("decision") == "approved" for m in members), "next": _APPROVAL_TODAY,
            "actions": ["approve", "reject", "later"], "score": _score(len(ids), strength, last, now),
        })
    return cards


async def _struggle_cards(now: float) -> list[dict]:
    try:
        import struggle_proposals as sp

        items = await sp.list_proposals(status="proposed", limit=200)
    except Exception as exc:  # noqa: BLE001 - a missing table / DB blip leaves the other cards
        logger.info("evolution_inbox: struggle proposals unavailable (%s)", type(exc).__name__)
        return []
    cards = []
    for p in items:
        cls, component, tier = sp.QUEUE_CLASS.get(p["kind"], ("code", "unknown", 3))
        nxt = ("A yes queues these as router training examples. The retrain that would use them is switched off today, so nothing "
               "changes yet." if p["kind"] == "router_example" else "A yes records a task note for the maintainer; nothing changes by itself.")
        episodes = int(p.get("episodes") or 0)
        cards.append({
            "card": "s-" + str(p["id"])[:12], "source": "struggle", "ids": [p["id"]], "count": 1, "kind": p["kind"],
            "component": component, "change_class": cls, "risk_tier": tier, "noticed": str(p.get("summary") or "")[:240],
            "evidence": {"episodes": episodes, "events": int(p.get("events") or 0), "user_days": int(p.get("user_days") or 0)},
            "first_seen": _date(p.get("first_seen")), "last_seen": _date(p.get("last_seen")), "proposes": _proposes(cls, component, tier),
            "approved": False, "next": nxt, "actions": ["approve", "reject", "later"],
            "score": _score(1, episodes, float(p.get("last_seen") or now), now),
        })
    return cards


async def _failed_turn_cards(viewer_id: str, now: float) -> list[dict]:
    """The owner's OWN failed turns waiting for a label, grouped by the route Zoe now thinks was right. Counts only: labelling needs
    the member to see their own words, which this page never shows."""
    try:
        import turn_outcomes

        rev = await turn_outcomes.review(viewer_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("evolution_inbox: failed turns unavailable (%s)", type(exc).__name__)
        return []
    groups: dict[str, list[float]] = {}
    for it in rev.get("items") or []:
        if it.get("label_status") == "proposed":
            groups.setdefault(re.sub(r"[^a-z_]", "", str(it.get("proposed_label") or "unknown"))[:24] or "unknown", []).append(
                float(it.get("ts") or now))
    return [{
        "card": "t-" + label, "source": "failed_turns", "ids": [], "count": len(ts), "kind": "failed_turns", "component": "router",
        "change_class": "harness", "risk_tier": 1, "noticed": f"{len(ts)} of your own requests went to the wrong place; Zoe thinks they were about {label}",
        "evidence": {"turns": len(ts)}, "first_seen": _date(min(ts)), "last_seen": _date(max(ts)),
        "proposes": "Learn from these turns once you confirm the right answer for each.", "approved": False,
        "next": "Confirming them needs your own words on screen, and that screen is not built yet. Nothing is learned without you.",
        "actions": [], "score": _score(len(ts), 0, max(ts), now),
    } for label, ts in groups.items()]


def _measured(raw: Any) -> dict:
    vr = eq._json(raw)
    if vr.get("schema") != 2:          # a pre-2026-11-13 verdict "validated" whatever it measured: it is not evidence
        return {"result": "not_measurable", "reason": "legacy_verdict"}
    word = {"validated": "better", "no_change": "no_change", "regressed": "worse"}.get(vr.get("verdict"), "not_measurable")
    return {"result": word, "reason": str(vr.get("reason") or "")[:40]}


async def inbox(viewer_id: str, *, stale_days: float = STALE_DAYS, now: Optional[float] = None) -> dict:
    t = time.time() if now is None else float(now)
    cutoff = t - stale_days * 86400.0
    async with eq._ctx() as db:
        rows = await _queue_rows(db)
        past = await eq._rows(db, "SELECT id, component, decision, status, validation_result, updated_at FROM evolution_proposals "
                                  "WHERE decision = 'approved' OR validation_result IS NOT NULL ORDER BY updated_at DESC LIMIT 10")
        snoozed_struggle = {eq._vals(r, 1)[0] for r in await eq._rows(
            db, "SELECT id FROM evolution_proposals WHERE source = 'struggle' AND next_review_at > ?", (t,))}
    later = [r for r in rows if r.get("next_review_at") and float(r["next_review_at"]) > t]
    snoozed = {r["id"] for r in later}
    stale = [r for r in rows if r["id"] not in snoozed and _last(r) < cutoff]
    live = [r for r in rows if r["id"] not in snoozed and _last(r) >= cutoff]
    struggle = [c for c in await _struggle_cards(t) if c["ids"][0] not in snoozed_struggle]
    cards = _queue_cards(live, t) + struggle + await _failed_turn_cards(viewer_id, t)
    cards.sort(key=lambda c: (-c["score"], c["card"]))
    decided = []
    for r in past:
        pid, component, decision, status, vr, upd = eq._vals(r, 6)
        decided.append({"id": pid, "component": component, "decision": decision or "", "status": status,
                        "measured": _measured(vr) if vr else {"result": "not_yet", "reason": "not_applied"}, "at": _date(upd)})
    return {"cards": cards[:CARD_CAP], "more_cards": max(0, len(cards) - CARD_CAP), "open": len(rows),
            "stale": {"count": len(stale), "days": stale_days}, "later": len(later) + len(snoozed_struggle), "decided": decided,
            "approval_does": _APPROVAL_TODAY}


async def _commit(db) -> None:
    fn = getattr(db, "commit", None)
    if callable(fn):
        await fn()


async def decide(ids: list[str], action: str, actor: str, reason: str = "", *, now: Optional[float] = None) -> dict:
    """approve | reject | later for every id of one card. Returns ``{"changed", "unchanged", "missing"}``; idempotent."""
    if action not in ("approve", "reject", "later"):
        raise ValueError("action must be approve, reject or later")
    if reason not in REASONS:
        raise ValueError("reason must be a known code")
    ids = sorted({str(i) for i in ids if i})[:MAX_IDS]
    t = time.time() if now is None else float(now)
    who = str(actor or "")[:80]
    changed = unchanged = 0
    struggle: list[str] = []
    async with eq._ctx() as db:
        found = {}
        for pid in ids:
            got = await eq._rows(db, "SELECT status, decision, source, next_review_at FROM evolution_proposals WHERE id = ?", (pid,))
            if got:
                found[pid] = dict(zip(("status", "decision", "source", "next_review_at"), eq._vals(got[0], 4)))
        for pid, r in found.items():
            if r["status"] not in eq.OPEN:
                unchanged += 1
            elif r["source"] == "struggle" and action != "later":
                struggle.append(pid)
            elif action == "approve" and r["decision"] == "approved":
                unchanged += 1
            elif action == "later" and r["next_review_at"] and float(r["next_review_at"]) > t:
                unchanged += 1
            else:
                try:
                    if action == "approve":
                        await eq.decide(db, pid, "approved", actor=who, note=reason, guard={"decision": ""}, now=t)
                    elif action == "reject":
                        await eq.transition(db, pid, "rejected", actor=who, note=reason or "rejected", now=t)
                    else:
                        async with eq.atomic(db):
                            await db.execute("UPDATE evolution_proposals SET next_review_at = ? WHERE id = ?", (t + LATER_DAYS * 86400.0, pid))
                            await eq.decide(db, pid, "", actor=who, note="later", now=t)
                    changed += 1
                except (eq.Conflict, eq.IllegalTransition):
                    unchanged += 1
        await _commit(db)
    if struggle:
        import struggle_proposals as sp

        for pid in struggle:
            try:
                await sp.decide(pid, "accept" if action == "approve" else "reject", who, reason)
                changed += 1
            except (sp.Conflict, sp.NotFound):
                unchanged += 1
    logger.info("evolution_decide: actor=%s action=%s changed=%d unchanged=%d missing=%d", who, action, changed, unchanged,
                len(ids) - len(found))
    return {"action": action, "changed": changed, "unchanged": unchanged, "missing": len(ids) - len(found)}


async def dismiss_stale(actor: str, *, stale_days: float = STALE_DAYS, now: Optional[float] = None) -> dict:
    """Reject every open non-struggle proposal with no activity for ``stale_days`` (not one snoozed with "later")."""
    t = time.time() if now is None else float(now)
    who = str(actor or "")[:80]
    n = 0
    async with eq._ctx() as db:
        rows = await _queue_rows(db)
        for r in rows:
            if _last(r) >= t - stale_days * 86400.0 or (r.get("next_review_at") and float(r["next_review_at"]) > t):
                continue
            try:
                await eq.transition(db, r["id"], "rejected", actor=who, note="dismissed: stale", now=t)
                n += 1
            except (eq.Conflict, eq.IllegalTransition):
                continue
        await _commit(db)
    logger.info("evolution_decide: actor=%s action=dismiss_stale days=%s dismissed=%d", who, stale_days, n)
    return {"dismissed": n, "days": stale_days}

