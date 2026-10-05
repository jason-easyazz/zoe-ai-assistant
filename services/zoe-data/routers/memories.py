"""Semantic-first memories API.

Every operation here lives in MemPalace, reached through `MemoryService`. The
earlier SQLite `memory_items` mirror has been retired: proposals land as
`status='pending'` rows in MemPalace, review flips the status, and
search/list read back through the service with per-user scoping.

See `docs/architecture/memory.md` for the full design.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from auth import (
    _has_valid_internal_token,
    get_current_user,
    require_admin,
    require_internal_token,
)
from database import get_db
from guest_policy import require_feature_access
from memory_service import (
    IndexCompactionError,
    MemoryRef,
    MemoryService,
    MemoryServiceError,
    get_memory_service,
    index_compaction_enabled,
    memory_affect,
)
from models import MemoryProposalCreate, MemoryReviewBody
import recall_evidence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])

_MAX_SEARCH_QUERY_LENGTH = 500
_MAX_LIKE_QUERY_LENGTH = 4096
_MAX_PROMPT_MESSAGE_LENGTH = 1000


# ─── Helpers ─────────────────────────────────────────────────────────────

_STATUS_ALIASES = {
    # Accept both the new canonical statuses and the legacy `memory_items`
    # values so the review UI doesn't need to change in lockstep.
    "pending_review": "pending",
    "pending": "pending",
    "approved": "approved",
    "rejected": "rejected",
    "archived": "archived",
    "superseded": "superseded",
}


def _ref_to_dict(ref: MemoryRef) -> dict[str, Any]:
    """Serialise MemoryRef for HTTP, keeping the legacy shape where practical.

    The journal / memories UIs expect `id`, `content`, `memory_type`, and a
    few other fields that used to come from `memory_items`. We map MemPalace
    metadata back into that shape so we don't have to rev every consumer in
    the same PR.
    """
    meta = ref.metadata or {}
    return {
        "id": ref.id,
        "user_id": meta.get("user_id") or meta.get("wing"),
        "memory_type": meta.get("memory_type", "fact"),
        "content": ref.text,
        "title": meta.get("title"),
        "entity_type": meta.get("entity_type"),
        "entity_id": meta.get("entity_id"),
        "confidence": float(meta.get("confidence", 0.0) or 0.0),
        "source_type": meta.get("source"),
        "source_id": meta.get("session_id") or meta.get("user_turn_id"),
        "source_excerpt": meta.get("source_excerpt"),
        "visibility": meta.get("visibility", "personal"),
        "status": meta.get("status", "approved"),
        "tags": [t for t in str(meta.get("tags", "") or "").split(",") if t],
        "observed_at": meta.get("added_at"),
        "last_verified_at": meta.get("reviewed_at"),
        "reviewed_by": meta.get("reviewed_by"),
        "reviewed_at": meta.get("reviewed_at"),
        "review_note": meta.get("review_note"),
        "created_at": meta.get("added_at"),
        "updated_at": meta.get("reviewed_at") or meta.get("added_at"),
        "expires_at": meta.get("expires_at"),
        "supersedes_id": meta.get("supersedes_id"),
        "contradicts_id": meta.get("contradicts_id"),
        "authority_class": meta.get("authority_class"),
        "origin": meta.get("origin"),
        "superseded_by_id": meta.get("superseded_by_id"),
        "access_count": int(meta.get("access_count", 0) or 0),
        "source": "mempalace",
    }


def _normalise_status(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = raw.lower().strip()
    return _STATUS_ALIASES.get(key, key)


def _svc() -> MemoryService:
    return get_memory_service()


# ─── Endpoints ───────────────────────────────────────────────────────────


@router.get("/")
async def list_memories(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List memories for the caller, optionally filtered by status.

    Status defaults to `approved` so the UI "my memories" tab doesn't see
    pending / rejected rows unless it asks.
    """
    await require_feature_access(db, user, feature="memories", action="read")
    svc = _svc()
    filter_status = _normalise_status(status) or "approved"
    rows = await svc.list_by_status(
        user_id=user["user_id"],
        status=filter_status,
        limit=limit,
        offset=offset,
    )
    memories = [_ref_to_dict(r) for r in rows]
    return {"memories": memories, "count": len(memories)}


@router.post("/proposals")
async def create_memory_proposal(
    body: MemoryProposalCreate,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Create a memory proposal.

    High-confidence preferences auto-approve (same heuristic as before); all
    other proposals land as `status='pending'` and surface in the review
    queue. The write goes straight into MemPalace — no SQLite mirror.
    """
    await require_feature_access(db, user, feature="memories", action="write")
    svc = _svc()
    auto_approve = body.confidence >= 0.9 and body.memory_type == "preference"
    status = "approved" if auto_approve else "pending"
    tags = ["zoe-memory", body.memory_type or "fact"]
    if body.source_type:
        tags.append(f"src:{body.source_type}")
    try:
        ref = await svc.ingest(
            body.content,
            user_id=user["user_id"],
            source=body.source_type or "proposal",
            # the proposals API is the person typing: its class must not depend on a
            # client-chosen source label ("manual", "web", anything)
            origin="proposal",
            memory_type=body.memory_type or "fact",
            confidence=float(body.confidence or 0.5),
            status=status,
            tags=tags,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
        )
    except MemoryServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if ref is None:
        # Silent drops (PII / dedup / opt-out) return 202 so the caller can
        # distinguish "we took no action" from a hard failure.
        return JSONResponse(
            status_code=202,
            content={"status": "dropped", "reason": "pii_or_dedup"},
        )
    return _ref_to_dict(ref)


@router.get("/review")
async def list_review_queue(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Surface rows awaiting human review for the current user."""
    await require_feature_access(db, user, feature="memories", action="review")
    svc = _svc()
    rows = await svc.list_by_status(
        user_id=user["user_id"], status="pending", limit=limit
    )
    items = [_ref_to_dict(r) for r in rows]
    # A write that disagreed with something the person said is parked as `disputed`
    # (memory_authority): show it HERE, beside the row it disputes, so it can be answered.
    for r in await svc.list_by_status(user_id=user["user_id"], status="disputed", limit=limit):
        d = _ref_to_dict(r)
        d["dispute"] = True
        old = await svc.get(str(r.metadata.get("contradicts_id") or "")) if r.metadata.get("contradicts_id") else None
        d["contradicts_text"] = old.text if old is not None else (r.metadata.get("edge_old_text") or None)
        items.append(d)
    return {"items": items, "count": len(items)}


@router.post("/{memory_id}/review")
async def review_memory(
    memory_id: str,
    body: MemoryReviewBody,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await require_feature_access(db, user, feature="memories", action="review")
    action = (body.action or "").lower().strip()
    if action not in {"approve", "reject", "edit"}:
        raise HTTPException(status_code=400, detail="action must be approve|reject|edit")
    svc = _svc()
    # Safety: callers can only review their own memories unless they're admin.
    current = await svc.get(memory_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    owner = current.metadata.get("user_id") or current.metadata.get("wing")
    is_admin = (user.get("role") or "").lower() == "admin"
    if owner and owner != user["user_id"] and not is_admin:
        raise HTTPException(status_code=403, detail="Cannot review another user's memory")
    try:
        ref = await svc.review(
            memory_id,
            decision=action,
            actor=user["user_id"],
            edits=body.content,
            note=body.note,
            # an admin reviewing ANOTHER user's row acts as an operator, not as the account
            # (memory_authority: only the account itself is user_confirmed on its own rows)
            origin="admin" if (is_admin and owner and owner != user["user_id"]) else None,
        )
    except MemoryServiceError as exc:
        # ValueErrors from bad input become 400, missing-row becomes 404.
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    if ref is None:  # the opt-out / identity / authority walls refuse with None
        raise HTTPException(status_code=409, detail="That change was not applied (held back).")
    return _ref_to_dict(ref)


@router.get("/search")
async def search_memories(
    q: str = Query(..., min_length=1, max_length=_MAX_SEARCH_QUERY_LENGTH),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Semantic search over MemPalace scoped to the caller's user_id."""
    await require_feature_access(db, user, feature="memories", action="read")
    svc = _svc()
    hits = await svc.search(q, user_id=user["user_id"], limit=limit)
    results = []
    for ref in hits:
        row = _ref_to_dict(ref)
        row["score"] = ref.score
        results.append(row)
    return {"query": q, "results": results, "count": len(results)}


_PROMPT_PACKET_MAX_FACTS = 12


# Near-duplicate collapse for the packet. The store still holds near-dupes the
# write-time gate didn't merge ("My mum likes ncis." / "your mum likes NCIS.")
# which otherwise waste the small packet's slots. Compare content tokens
# (stopwords stripped) — collapse only clear repeats, never distinct or *richer*
# facts (a superset like "My dad's name is Neil. My mum likes ncis. I have two
# sisters…" is kept alongside "My dad's name is Neil" — it carries more).
_DEDUP_STOPWORDS = frozenset({
    "the", "a", "an", "is", "am", "are", "was", "were", "be", "been", "my", "your",
    "our", "i", "you", "we", "of", "to", "in", "on", "at", "and", "or", "that",
    "this", "it", "s", "for", "with", "has", "have", "had",
})


def _dedup_tokens(text: str) -> frozenset:
    return frozenset(
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) > 1 and t not in _DEDUP_STOPWORDS
    )


def _is_near_duplicate(cand: frozenset, kept: list) -> bool:
    """True when `cand` adds nothing over an already-kept line — i.e. it is a
    near-repeat. Drops only when the CANDIDATE's own tokens are (near-)fully
    covered by a kept line (``inter / len(cand) >= 0.85``). This coverage test is
    deliberately ASYMMETRIC and the sole criterion: a *richer* candidate (a
    superset with genuinely new tokens — e.g. one that adds a location) is not
    covered, so it survives; dropping it would lose information. (A symmetric
    jaccard test would wrongly drop such a candidate, so it is intentionally not
    used.) Tiny (<2 content-token) facts are never collapsed."""
    if len(cand) < 2:
        return False
    for other in kept:
        if len(other) < 2:
            continue
        if len(cand & other) / len(cand) >= 0.85:
            return True
    return False


# ── Conflict-aware recency presentation ─────────────────────────────────────
#
# Live bug (2026-07-07): the packet listed stale facts ("sister ... Katie",
# twice) ABOVE the newer correction ("sister named Kate"), so the brain answered
# with the superseded value despite the newest-wins recall doctrine. Selection
# is relevance-ranked (hits by semantic blend, facts by confidence×decay +
# access hotness), and an old, often-accessed fact legitimately outranks a
# fresh correction — so relevance stays the SELECTOR, but when two selected
# bullets look like the SAME underlying fact with a changed value, the group is
# PRESENTED newest-first (by stored `added_at`). Packets with no conflicting
# bullets are byte-for-byte unchanged.
#
# Deliberately NOT collapsed to one bullet: token overlap alone cannot tell a
# changed value ("lives in Geraldton" → "lives in Perth") from complementary
# facts about the same subject ("Kate likes tennis" / "Kate likes running") —
# distinguishing those needs fuzzy/semantic matching, which is out of scope for
# this hot read path. Pure rephrasings are already collapsed by the ≥0.85
# near-dup coverage test above.

_CONFLICT_MIN_OVERLAP = 0.6


def _added_at_ts(meta: dict[str, Any]) -> float:
    """Best-effort epoch seconds from `added_at`; missing/garbled → -inf so
    undated rows sort as oldest (and all-undated groups keep their order)."""
    raw = (meta or {}).get("added_at")
    if not raw:
        return float("-inf")
    try:
        dt = datetime.datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _is_conflicting_pair(a: frozenset, b: frozenset) -> bool:
    """True when two kept lines look like the same fact with a differing value:
    they share most of the smaller side's content tokens (≥ 0.6) while EACH side
    still has tokens the other lacks (a strict subset is a richer/poorer phrasing
    pair, not a contradiction). Tiny (<2 content-token) lines never conflict,
    mirroring the near-dup guard."""
    if len(a) < 2 or len(b) < 2:
        return False
    inter = len(a & b)
    if inter == len(a) or inter == len(b):  # subset ⇒ enrichment, not conflict
        return False
    return inter / min(len(a), len(b)) >= _CONFLICT_MIN_OVERLAP


def _present_conflicts_newest_first(
    lines: list[str],
    refs: list[dict[str, Any]],
    tokens: list[frozenset],
    ts: list[float],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Reorder ONLY conflicting bullets newest-first, in place of their slots.

    Conflicting lines are grouped transitively (union-find); each group's
    members are re-dealt into the group's original positions ordered by
    `added_at` descending (stable — undated/tied rows keep selection order).
    Non-conflicting lines keep their exact positions, so a packet with no
    conflicts is returned unchanged.

    Deliberate: this operates on the FLAT bullet list, so a conflict spanning
    the hits/facts boundary can demote a stale search hit below a newer
    general-fact correction — that is the point of the fix (the live bug was a
    relevance-ranked stale value shadowing the correction). Recency outranks
    the hits-lead convention ONLY within a conflict group; each ref still
    carries its truthful `from_search` flag.
    """
    n = len(lines)
    if n < 2:
        return lines, refs

    parent = list(range(n))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    any_conflict = False
    for i in range(n):
        for j in range(i + 1, n):
            if _is_conflicting_pair(tokens[i], tokens[j]):
                ri, rj = _find(i), _find(j)
                if ri != rj:
                    parent[rj] = ri
                any_conflict = True
    if not any_conflict:
        return lines, refs

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(_find(i), []).append(i)

    order = list(range(n))
    for members in groups.values():
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda i: ts[i], reverse=True)
        for slot, src in zip(members, ranked):
            order[slot] = src
    return [lines[i] for i in order], [refs[i] for i in order]


def _emotional_intensity(ref: MemoryRef) -> float:
    """Sort key: an `emotional_moment`'s stored intensity (0..1), else -1 so
    non-emotional facts sort after. Reads `candidate_intensity` (where the
    memory_store intent parks the signal); missing/garbled → 0.0 for an emotional
    row so it still floats ahead of plain facts, just behind scored ones."""
    meta = ref.metadata or {}
    if str(meta.get("memory_type")) != "emotional_moment":
        return -1.0
    try:
        return float(meta.get("candidate_intensity"))
    except (TypeError, ValueError):
        return 0.0


def _build_memory_prompt_packet(
    facts: list[MemoryRef],
    hits: list[MemoryRef],
    *,
    max_facts: int = _PROMPT_PACKET_MAX_FACTS,
    boost_emotional: bool = False,
    recent: Optional[list[MemoryRef]] = None,
    evidence: bool = False,
    quotes: bool = False,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Compile a compact, cited memory packet for system-prompt injection.

    Honors the Samantha memory prompt-policy: a small packet (not a raw dump),
    every line carries a source/evidence id, superseded/archived facts are
    dropped (prefer current), and disputed facts are surfaced as uncertain.
    Message-relevant semantic hits lead; general facts follow. Conflicting
    bullets (same fact, changed value) are presented newest-first within their
    slots (see ``_present_conflicts_newest_first``) so a correction always
    appears above its stale sibling.

    When ``boost_emotional`` is set (ZOE_EMOTIONAL_RECALL_ENABLED, Samantha
    criterion #2), `emotional_moment` rows are floated to the front of the
    *generic* section — behind semantic hits, ahead of plain facts, ordered by
    intensity — so a heavy user's emotional continuity isn't crowded out of the
    small packet by ordinary facts. A stable sort keeps existing order otherwise;
    OFF is a byte-for-byte no-op.

    ``recent`` (continuity mode only, see ``_pick_recent_for_continuity``) is
    considered FIRST — ahead of semantic hits — and each of its lines carries a
    ``(recent)`` prefix so the brain can tell "shared in the last few days" from
    a long-standing fact. None/empty (every non-continuity caller) is a
    byte-for-byte no-op.

    ``evidence`` (ZOE_RECALL_EVIDENCE, see ``recall_evidence``) dates each
    bullet ``(Mon 22 Sep, 8 days ago)`` before its cite and adds one
    instruction line under the authority rule; ``quotes`` additionally appends
    ``— you said: "…"`` to the first ``MAX_QUOTES`` bullets (presented order)
    that have a quotable excerpt, one quote per distinct excerpt. False (the
    default, and every caller while the flag is off) is a byte-for-byte no-op.
    """
    if boost_emotional and facts:
        facts = sorted(facts, key=_emotional_intensity, reverse=True)
    seen: set[str] = set()
    kept_tokens: list[frozenset] = []
    kept_ts: list[float] = []
    lines: list[str] = []
    refs: list[dict[str, Any]] = []
    quote_by_id: dict[str, str] = {}
    dated = 0

    def _consider(ref: MemoryRef, *, from_search: bool, is_recent: bool = False) -> None:
        nonlocal dated
        if len(lines) >= max_facts:
            return
        meta = ref.metadata or {}
        status = str(meta.get("status") or "active").lower()
        # Drop-set mirrors memory_service._BLOCKED_READ_STATUSES *except* "disputed",
        # which the Samantha prompt-policy surfaces as "(uncertain)" rather than
        # hiding. NOTE: today's callers (load_for_prompt / search) already pre-filter
        # disputed at the service layer, so the disputed branch below is exercised
        # only by future direct callers (e.g. Hindsight/Graphiti passing refs in) —
        # it is intentional policy, not dead code. Keep this drop-set in sync with
        # the service block list if statuses change.
        if status in {"superseded", "archived", "rejected", "pending"}:
            return
        text = (ref.text or "").strip()
        if not text or ref.id in seen:
            return
        # Collapse near-duplicate content (the store still holds un-merged
        # near-dupes). Search hits are considered first, so the higher-ranked
        # phrasing of a repeated fact wins its slot.
        tokens = _dedup_tokens(text)
        if _is_near_duplicate(tokens, kept_tokens):
            return
        seen.add(ref.id)
        kept_tokens.append(tokens)
        kept_ts.append(_added_at_ts(meta))
        cite = f"[mem:{str(ref.id)[:8]}]"
        prefix = "(uncertain) " if status == "disputed" else ""
        if str(meta.get("memory_type")) == "state_change":
            # memory_supersede's tombstone: a recorded CHANGE, not a current fact.
            prefix = f"(change) {prefix}"
        if is_recent:
            felt = memory_affect(ref)
            prefix = f"(recent, felt {felt}) {prefix}" if felt else f"(recent) {prefix}"
        when = recall_evidence.date_suffix(meta, now=now) if evidence else ""
        dated += bool(when)
        if quotes:
            quote_by_id[ref.id] = recall_evidence.quote_for(meta, text[:200])
        lines.append(f"- {prefix}{text[:200]}{when} {cite}")
        entry = {
            "id": ref.id,
            "memory_type": meta.get("memory_type", "fact"),
            "status": status,
            "from_search": from_search,
        }
        if is_recent:
            entry["recent"] = True
        refs.append(entry)

    for ref in recent or ():
        _consider(ref, from_search=False, is_recent=True)
    for ref in hits:
        _consider(ref, from_search=True)
    for ref in facts:
        _consider(ref, from_search=False)

    if not lines:
        return {"packet": "", "refs": [], "count": 0}
    # Newest-wins presentation: relevance selected the bullets above; when two
    # selected bullets contradict (same fact, changed value), the newer one is
    # presented first so the brain's newest-wins doctrine sees the correction
    # before the stale sibling. No conflicts ⇒ byte-for-byte unchanged.
    lines, refs = _present_conflicts_newest_first(lines, refs, kept_tokens, kept_ts)
    # Quotes go on the first bullets AS PRESENTED (after the newest-first
    # reorder), so a correction carries its words above its stale sibling.
    quoted: set[str] = set()
    for i, entry in enumerate(refs):
        if len(quoted) >= recall_evidence.MAX_QUOTES:
            break
        words = quote_by_id.get(entry["id"]) or ""
        if words and words not in quoted:
            quoted.add(words)
            lines[i] = f'{lines[i]} — you said: "{words}"'
    # Denial-echo guard: this packet is injected every turn, but in a long-lived
    # session the model's OWN earlier "I don't have any information about X"
    # replies sit in the conversation context and can outvote the packet on
    # retries (observed live 2026-07-12: the facts ranked #1-2 in this packet,
    # yet three prior denials kept winning). Travel an explicit authority rule
    # WITH the facts so recalled memory beats stale conversational denials.
    # The header line stays first — consumers pin
    # `startswith("## What I know about you")`.
    guide = f"{recall_evidence.instruction_line(quotes=bool(quoted))}\n" if evidence else ""
    result: dict[str, Any] = {
        "packet": (
            "## What I know about you\n"
            "(These stored memories are authoritative and current. If anything "
            "said earlier in this conversation conflicts with them — including "
            "your own earlier replies that information was unknown or not on "
            "file — trust these memories and answer from them.)\n"
            + guide
            + "\n".join(lines)
        ),
        "refs": refs,
        "count": len(refs),
    }
    if evidence:
        result["evidence"] = {"dated": dated, "quoted": len(quoted)}
    return result


def _fold_relational_block(
    packet: dict[str, Any], block: dict[str, Any]
) -> dict[str, Any]:
    """Fold the 2b relational block under the vector packet, keeping it cited.

    The vector packet keeps its ``## What I know about you`` section (verbatim);
    the relational lines are appended under a second ``## People & important
    dates`` heading so provenance stays visible (each line already carries a
    ``[people]`` / ``[relationship]`` / ``[date]`` / ``[portrait]`` tag). ``refs``
    and ``count`` are extended, and ``relational`` records how many relational
    refs were added so tests/callers can see the gate fired. Shape-compatible
    with the existing consumer (``memory.ts`` reads only ``packet``).
    """
    lines = block.get("lines") or []
    refs = block.get("refs") or []
    if not lines:
        return packet
    section = "## People & important dates\n" + "\n".join(lines)
    existing = packet.get("packet") or ""
    packet["packet"] = f"{existing}\n\n{section}" if existing else section
    packet["refs"] = list(packet.get("refs") or []) + list(refs)
    packet["count"] = len(packet["refs"])
    packet["relational"] = len(refs)
    return packet


_PENDING_CONTACTS_MAX = 3
_PENDING_FIELD_CAP = 60


def _safe_prompt_inline(s: str, cap: int = _PENDING_FIELD_CAP) -> str:
    """Neutralise extracted text before it enters the prompt: collapse all
    whitespace (kills newline-based section injection), drop markdown/structure
    chars a value could use to fake headings/citations, and cap length. Extracted
    names/relationships are user-derived, so treat them as untrusted."""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    # Quotes stripped too: values land INSIDE the quoted 'Ask the user: "…"'
    # directive, so an embedded quote could close it and inject instructions
    # (same fix as zoe_flue_client._pending_offer_block's sanitizer).
    s = re.sub(r"[#`*_\[\]\n\r{}\"'\u2018\u2019\u201c\u201d]", "", s)
    return s[:cap]


async def _fold_pending_contact_offers(packet: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Surface pending `person_create` proposals into the for-prompt packet so the
    brain (incl. flue) can OFFER them — the "Observe & Suggest" rung of the
    human-in-the-loop ladder (ADR-contacts-production-hardening P1).

    Closes the live gap: `detect_and_store` creates a proposal on a passive
    mention, but the flue brain reads this packet and never saw it (offers were
    injected only via the legacy zoe_agent path). User-scoped
    (`list_pending_contacts`) so it surfaces regardless of the active session.
    Flag-gated (`ZOE_PERSON_SUGGEST_ENABLED`); OFF is a byte-for-byte no-op — the
    read is skipped entirely. Best-effort: never raises out of the endpoint.
    """
    try:
        from pending_suggestions import (
            surface_pending_contacts_for_prompt,
            person_suggestions_enabled,
        )
        if not person_suggestions_enabled():
            return packet
        # Non-destructive read: surfacing marks the offer as seen; aging happens
        # once per real user turn (age_person_offers_on_user_turn), never here.
        import contacts_conversation as _cc
        batch = _cc.offer_batch_enabled()
        pend = await surface_pending_contacts_for_prompt(
            user_id, limit=_cc.OFFER_BATCH_MAX if batch else _PENDING_CONTACTS_MAX)
    except Exception:
        logger.exception("memories: pending-contact offer fold failed (user=%s)", user_id)
        return packet
    if batch:
        # ZOE_CONTACT_OFFER_BATCH: ONE enumerated question for the whole set
        # (a single yes then accepts all of it - intent_router batch matcher).
        q = _cc.offer_question(pend, _safe_prompt_inline)
        if not q:
            return packet
        _cc.record_asked(user_id, q, pend)  # the set a following yes/no may bind to
        section = (
            "## People mentioned recently (not contacts yet)\n"
            "IMPORTANT: In this reply, after answering the user, ask the question "
            "below word-for-word, as ONE question. If the user answers yes, the "
            "contacts are saved for them automatically (you may also use the "
            "people_create tool); if they say no, drop it and don't ask again.\n"
            f'- Ask the user: "{q}" [pending-contact]'
        )
        existing = packet.get("packet") or ""
        packet["packet"] = f"{existing}\n\n{section}" if existing else section
        return packet
    bullets = []
    for p in pend:
        name = _safe_prompt_inline(p.get("name") or "")
        if not name:
            continue
        rel = _safe_prompt_inline(p.get("relationship") or "")
        # Give the 4B model the exact question to voice — the earlier soft
        # "you may offer to add them" phrasing was never voiced in live review
        # (QA review F5b). One explicit ask-this line per offer.
        question = f"Would you like me to add {name}{f' (your {rel})' if rel else ''} as a contact?"
        bullets.append(f'- Ask the user: "{question}" [pending-contact]')
    if not bullets:
        return packet
    section = (
        "## People mentioned recently (not contacts yet)\n"
        "IMPORTANT: In this reply, after answering the user, ask the FIRST question "
        "below word-for-word. If the user answers yes, the contact is saved for them "
        "automatically (you may also use the people_create tool); if they say no, "
        "drop it and don't ask again.\n"
        + "\n".join(bullets)
    )
    existing = packet.get("packet") or ""
    packet["packet"] = f"{existing}\n\n{section}" if existing else section
    return packet


# Keyword gate for the per-turn semantic search. Single source of truth lives in
# memory_gate (shared with zoe_agent) so the two paths can't silently diverge. The
# ONNX+Chroma semantic search only fires when the message looks like a recall query
# — most turns don't, and the embed+query is the endpoint's main cost.
from memory_gate import message_needs_memory as _message_needs_memory  # noqa: E402
from memory_gate import message_needs_emotional_recall as _message_needs_emotional_recall  # noqa: E402


def _emotional_recall_enabled() -> bool:
    """Samantha criterion #2 recall wiring, default OFF. Per-call env read (matches
    the compose flag) so a restart flips it without code change. When OFF, emotional
    queries fall back to the base gate and the packet keeps default order — a true
    no-op — so this ships dark and is lab-proven on real rows before prod."""
    return os.getenv("ZOE_EMOTIONAL_RECALL_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


# On an emotional turn we PIN the user's emotional moments into the packet rather
# than trust generic semantic ranking. Live-testing showed why: for a topical
# query the settlement-anxiety row matches, but for a *generic* emotional query
# ("how have I been doing") there's no lexical overlap, so search ranks ordinary
# facts above it — and load_for_prompt's top-N may have already truncated it out.
# An explicit type-filtered pick, ordered by intensity, guarantees a slot.
_EMO_PIN_SCAN = 200   # wider read window (emotional turns only) to see rows past the top-N
_EMO_PIN_MAX = 3      # how many emotional moments to guarantee in the packet


def _pick_emotional_moments(rows: list[MemoryRef]) -> list[MemoryRef]:
    """Top emotional_moment rows from an already-loaded slice, highest-intensity
    first. Pure filter over rows the caller already read (no extra store round-trip)
    — on an emotional turn the endpoint widens its single load_for_prompt to
    `_EMO_PIN_SCAN` and feeds the result here, so a crowded-out emotional row is
    still found without a second read."""
    emo = [r for r in rows if str((r.metadata or {}).get("memory_type")) == "emotional_moment"]
    emo.sort(key=_emotional_intensity, reverse=True)
    return emo[:_EMO_PIN_MAX]


# ── Continuity mode (Samantha bar S4, the emotional thread) ──────────────────
#
# A mood STATEMENT ("ugh, I've been feeling a bit on edge today") shares almost
# no words with yesterday's worry ("anxious about my job interview at the
# aquarium"), so relevance ranking — semantic blend for hits, confidence×decay
# plus access hotness for facts — has nothing to latch onto and the worry is not
# in the packet. Continuity is about WHEN something was said, not what it
# matches: in `mode="continuity"` the composer pins what the user shared in the
# last few days ahead of everything else, emotional rows first. Only the flue
# seam's continuity trigger asks for this mode; every other caller gets the
# relevance packet, unchanged.
_PACKET_MODE_CONTINUITY = "continuity"
_CONTINUITY_RECENT_WINDOW_S = 72 * 3600  # "the last few days" — covers a day-1 → day-2 gap
_CONTINUITY_RECENT_MAX = 6  # at most half the default packet, so relevance keeps room
# Bound on the direct recency read (newest-first, inside the window). Recency is
# read DIRECTLY — not filtered out of the ranked load_for_prompt prefix, where a
# heavy user's yesterday can rank below any fixed cut.
_CONTINUITY_RECENT_SCAN = 50


def _is_emotional_row(ref: MemoryRef) -> bool:
    """See ``memory_service.is_emotional_memory`` (the single definition — the
    service's recency read orders by it too)."""
    from memory_service import is_emotional_memory

    return is_emotional_memory(ref)


def _pick_recent_for_continuity(
    rows: list[MemoryRef],
    *,
    now_ts: Optional[float] = None,
    window_s: float = _CONTINUITY_RECENT_WINDOW_S,
    max_n: int = _CONTINUITY_RECENT_MAX,
) -> list[MemoryRef]:
    """Rows captured within ``window_s`` of ``now_ts`` (by stored `added_at`),
    emotional rows first, then newest first; at most ``max_n``. Pure filter over
    rows the caller already loaded — no store round-trip. Undated rows never
    qualify (``_added_at_ts`` → -inf)."""
    now = datetime.datetime.now(datetime.timezone.utc).timestamp() if now_ts is None else now_ts
    cutoff = now - window_s
    recent = [(r, _added_at_ts(r.metadata or {})) for r in rows]
    recent = [(r, ts) for r, ts in recent if cutoff <= ts <= now + 300]
    recent.sort(key=lambda p: (_is_emotional_row(p[0]), p[1]), reverse=True)
    return [r for r, _ in recent[:max_n]]


def _continuity_focus(recent: list[MemoryRef], refs: list[dict[str, Any]]) -> dict[str, str]:
    """The ONE recent emotional item a continuity turn should check in about:
    the first recent row (already emotional-first, then newest) that is
    emotional and made it into the packet. {} when there is none — a neutral
    recent fact is never promoted to a check-in ("how is the haircut going?").
    A 4B model given eleven bullets and a soft "connect if relevant" rarely
    picks the worry; given one concrete item it does.

    A bare mood report ("User has been feeling a bit on edge today") is never
    the focus either (``memory_digest.fact_has_topic``): it is usually what the
    user is saying right now — the digest stores today's mood statement seconds
    after the turn, so it becomes the newest emotional row and would displace
    the real worry on the very next mood turn ("how is feeling on edge going?").
    Measured live on the Samantha bar S4 round 3: sample 0 checked in about the
    interview, samples 1-2 (after the mood row landed) did not."""
    from memory_digest import fact_has_topic

    kept = {r.get("id") for r in refs}
    for ref in recent:
        if ref.id in kept and _is_emotional_row(ref):
            text = re.sub(r"\s+", " ", ref.text or "").strip()[:200]
            if text and fact_has_topic(text):
                return {"text": text, "affect": memory_affect(ref)}
    return {}


@router.get("/for-prompt")
async def memory_for_prompt(
    user_id: str = Query(..., min_length=1),
    message: str = Query(
        "",
        max_length=_MAX_PROMPT_MESSAGE_LENGTH,
        description="Current user message, for relevance ranking",
    ),
    limit: int = Query(_PROMPT_PACKET_MAX_FACTS, ge=1, le=40),
    mode: str = Query(
        "relevance",
        pattern="^(relevance|continuity)$",
        description="'continuity' pins facts from the last few days ahead of "
        "relevance (the flue seam's mood-statement turns)",
    ),
    _: None = Depends(require_internal_token),
):
    """Compact, cited memory packet for injection into an agent's system prompt.

    Internal/service endpoint (loopback or `X-Internal-Token`) — this is how the
    zoe-core Pi brain pulls Zoe's memory each turn. Fails closed: guest/unknown
    users get an empty packet. MemPalace-backed today; the Samantha plan's
    Hindsight/Graphiti layers compose into this same packet later.
    """
    from memory_service import is_guest_memory_user

    if not user_id or is_guest_memory_user(user_id):
        return {"packet": "", "refs": [], "count": 0, "user_scoped": False}
    svc = _svc()
    # Facts (cheap metadata read) always run. The semantic search is an ONNX embed
    # + Chroma query — keyword-gate it so it only fires on recall-ish turns instead
    # of every turn (the Pi memory extension calls this endpoint on every turn).
    # Emotional-recall wiring (Samantha #2), default OFF: when on, an emotional cue
    # ("how have I been", "feeling overwhelmed") fires the search AND pins the
    # user's emotional moments into the packet. Decide it first so a single
    # load_for_prompt can widen its window on an emotional turn — no second read.
    emo_turn = _emotional_recall_enabled() and _message_needs_emotional_recall(message)
    # In-process callers pass every argument explicitly; one that omits `mode`
    # receives the Query() descriptor, which is not the continuity string — so
    # the isinstance guard keeps them on the relevance packet.
    continuity = isinstance(mode, str) and mode == _PACKET_MODE_CONTINUITY
    # One metadata read. On an emotional turn we scan wider (_EMO_PIN_SCAN) so a
    # crowded-out emotional row is visible to the pin below; the generic packet
    # still uses only the first `limit` rows (load_for_prompt returns a stable
    # prefix, so this slice == the narrow read). Continuity reads recency
    # directly below, so it does not need the wide ranked scan.
    scan = _EMO_PIN_SCAN if emo_turn else limit
    all_rows = await svc.load_for_prompt(user_id, limit=scan)
    facts = all_rows[:limit]
    hits: list[MemoryRef] = []
    needs_search = _message_needs_memory(message) or emo_turn or continuity
    if message.strip() and needs_search:
        try:
            hits = await svc.search(message, user_id=user_id, limit=6)
        except Exception:
            logger.exception("memories: semantic prompt search failed")
            hits = []
    # On an emotional turn, PIN the user's emotional moments to the front of the
    # packet (ahead of semantic hits) so continuity survives even when generic
    # ranking would bury them. Filtered from the rows already loaded above — no
    # extra round-trip. Dedup by id in the packet builder means a moment search
    # also returned is not double-counted. Only on an emotional turn, so a
    # non-emotional turn stays a byte-for-byte no-op even with the flag on.
    if emo_turn:
        hits = _pick_emotional_moments(all_rows) + hits
    # Continuity mode: what was shared in the last few days leads the packet,
    # however little it overlaps the current message lexically.
    recent = None
    if continuity:
        try:
            recent_rows = await svc.load_recent_for_prompt(
                user_id,
                window_s=_CONTINUITY_RECENT_WINDOW_S,
                limit=_CONTINUITY_RECENT_SCAN,
                emotional_first=True,
            )
        except Exception:
            logger.exception("memories: continuity recency read failed")
            recent_rows = []
        # Merge with the ranked rows already loaded (dedup by id, recency read
        # first), then pick emotional-then-newest under the cap.
        seen_ids: set[str] = set()
        candidates: list[MemoryRef] = []
        for ref in list(recent_rows) + list(all_rows):
            if ref.id not in seen_ids:
                seen_ids.add(ref.id)
                candidates.append(ref)
        recent = _pick_recent_for_continuity(candidates)
    # Evidence-bearing recall (ZOE_RECALL_EVIDENCE, default OFF): dates on every
    # relevance packet — the floor, the recall_memory tool, the core lane —
    # and the user's own words on an evidence-shaped turn. Never in continuity
    # mode: that block is S4-tuned and budgeted around its closing ask.
    evidence = not continuity and recall_evidence.enabled()
    quotes = evidence and recall_evidence.wants_quotes(message, user_id)
    result = _build_memory_prompt_packet(
        facts, hits, max_facts=limit, boost_emotional=emo_turn, recent=recent,
        evidence=evidence, quotes=quotes,
    )
    if evidence:
        ev = result.pop("evidence", None) or {}
        logger.info("RECALL_EVIDENCE user=%s quotes=%d bullets=%d dated=%d quoted=%d chars=%d",
                    user_id, int(quotes), result.get("count", 0), ev.get("dated", 0),
                    ev.get("quoted", 0), len(result.get("packet") or ""))
    result["user_scoped"] = True
    if continuity:
        focus = _continuity_focus(recent or [], result.get("refs") or [])
        if focus:
            result["continuity_focus"] = focus

    # Increment 2b: fold the relational half (Postgres people/relationships/dates
    # + portrait) into the packet, behind ZOE_MEMORY_COMPOSE_ENABLED (default OFF)
    # and router-gated to relational queries. OFF (or a non-relational query) is a
    # true no-op: compose_packet() cheap-gates before any DB read, so the packet
    # above is returned byte-for-byte. The gate + DB context + block build live in
    # the shared zoe_memory_compose.compose_packet so chat and voice can't drift.
    # Best-effort — compose_packet never raises.
    from zoe_memory_compose import compose_packet

    block = await compose_packet(user_id, message)
    if block:
        result = _fold_relational_block(result, block)

    # P1 (ADR-contacts-production-hardening): surface pending "add contact?"
    # offers so the flue brain can proactively confirm them. Flag-gated no-op.
    # NOT in continuity mode: a mood turn's one job is the check-in, and with the
    # word-for-word directive in the packet the reply ended in "add Marisol as a
    # contact?" on every sample (Samantha bar S4 round 3). The offer is DEFERRED,
    # not lost: it is not surfaced here, so a not-yet-seen offer does not start
    # aging (surfacing marks it; the per-user-turn ager counts surfaced offers
    # only) and the next non-emotional turn offers it as usual.
    # The same deferral applies wherever the packet is built for a continuity
    # TURN — the core brain's in-process packet and memory.ts pass the user's
    # message in relevance mode — decided by the one trigger predicate the seam
    # and the offer ager use (Greptile #1768).
    if not continuity and not _is_continuity_turn(message, user_id):
        result = await _fold_pending_contact_offers(result, user_id)
    return result


def _is_continuity_turn(message: str, user_id: str) -> bool:
    """``zoe_flue_client.is_continuity_turn`` (pure trigger predicate), False
    if it cannot be evaluated — an unknown turn folds offers as before."""
    try:
        from zoe_flue_client import is_continuity_turn

        return is_continuity_turn(message if isinstance(message, str) else "", user_id)
    except Exception:  # noqa: BLE001
        return False


@router.post("/backfill-contacts")
async def backfill_contacts_endpoint(
    user_id: str = Query(..., min_length=1),
    session_id: str = Query(
        "backfill",
        min_length=1,
        description="Session the proposals are stored under. Pass the user's "
        "ACTIVE session so `list_active`/`load_for_prompt` surface them — the "
        "suggestions retrieval paths filter by session_id, so proposals left in "
        "the default 'backfill' session are never shown in a live chat.",
    ),
    _: None = Depends(require_internal_token),
    db=Depends(get_db),
):
    """One-shot admin pass: turn a user's known-but-not-a-contact people into
    accept-able `person_create` proposals (Phase 2b, ADR-contacts-from-known-people).

    Internal/service endpoint (loopback or `X-Internal-Token`), matching
    `/for-prompt`. Flag-gated behind `ZOE_CONTACT_BACKFILL_ENABLED`; a no-op that
    proposes nothing when the flag is off. Never creates contacts directly — it
    emits pending suggestions the user accepts through the suggestions UI.
    """
    from contact_backfill import backfill_contacts

    return await backfill_contacts(user_id, session_id=session_id, db=db)


@router.get("/pending-contacts")
async def pending_contacts_endpoint(
    user_id: str = Query(..., min_length=1),
    _: None = Depends(require_internal_token),
):
    """User-scoped review path for pending `person_create` proposals.

    Backfill (Phase 2b) stores proposals under a static `'backfill'` session, so
    the session-scoped `list_active`/`load_for_prompt` paths never surface them in
    a live chat. This session-agnostic endpoint lists every un-resolved contact
    proposal for the user so the UI can offer them regardless of the active session
    (accept is already keyed by id+user_id, so it works cross-session).

    Internal/service endpoint (loopback or `X-Internal-Token`), matching
    `/backfill-contacts`. Flag-gated behind `ZOE_CONTACT_BACKFILL_ENABLED`; fails
    closed with an empty list when the flag is off.
    """
    from contact_backfill import contact_backfill_enabled
    from pending_suggestions import list_pending_contacts

    if not contact_backfill_enabled():
        return {"pending": [], "count": 0}
    pending = await list_pending_contacts(user_id)
    return {"pending": pending, "count": len(pending)}


@router.get("/maintenance/index-health")
async def memory_index_health_endpoint(_: None = Depends(require_internal_token)):
    """Tombstone health of the drawers HNSW index — read-only, safe while serving.

    Internal/service endpoint (loopback or `X-Internal-Token`). Reads the palace SQLite
    (`mode=ro`) + the segment's `index_metadata.pickle` + the write-ahead log tail — no
    chroma call, so it never waits on the maintenance gate. `compaction_advised` (ratio ≥ 3)
    is what the weekly dreaming trigger acts on; it is `None` when the ratio is unknown
    (`ratio_known=False`), never guessed. `maintenance_blocked` + `maintenance_reason`
    report a gate that failed closed after an unverified restore (runbook §22).
    """
    try:
        return await _svc().index_health()
    except Exception as exc:  # noqa: BLE001 — a missing/unreadable palace is a 503, not a crash
        raise HTTPException(status_code=503, detail=f"index health unavailable: {exc}")


@router.post("/maintenance/compact-index")
async def memory_compact_index_endpoint(_: None = Depends(require_internal_token)):
    """Rebuild the drawers index from its STORED embeddings, in-process, no restart.

    Internal/service endpoint (loopback or `X-Internal-Token`), flag-gated behind
    `ZOE_MEMORY_INDEX_COMPACT` (default OFF → 404, so the route is dark until an operator
    flips it). Serialised: a second call while one runs — or in-flight collection work
    that does not drain within the budget — is 409 (`status="busy"`). Every other failure
    is a structured 500 whose body is the report: `status="aborted"` (nothing changed),
    `"restored"` (rows put back from the export, verified) or `"blocked"` (restore not
    verified: the gate stays closed, `maintenance_blocked=true`, operator recovery).
    """
    if not index_compaction_enabled():
        raise HTTPException(status_code=404, detail="memory index compaction is disabled (ZOE_MEMORY_INDEX_COMPACT)")
    try:
        return await _svc().compact_index()
    except IndexCompactionError as exc:
        status = 409 if exc.report.get("status") == "busy" else 500
        return JSONResponse(status_code=status, content=exc.report)


@router.get("/people")
async def people_with_memories(
    limit: int = Query(100, ge=1, le=500),
    q: Optional[str] = Query(
        None,
        max_length=_MAX_LIKE_QUERY_LENGTH,
        description="Optional name filter",
    ),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return people the journal UI can tag.

    Implements the endpoint `journal-ui-enhancements.js` has always called
    but which previously 404ed. Response shape matches the consumer:
    `{people: [{id,name,relationship,avatar_url}], count}`.

    Ordering is `lower(name)` — portable SQL. SQLite's NOCASE collation is SQLite-only
    and made this endpoint 500 on Postgres (`collation "nocase" ... does not
    exist`, 2026-09-25 audit §2.1) while the SQLite-backed unit lane stayed green.
    """
    await require_feature_access(db, user, feature="memories", action="read")
    user_id = user["user_id"]
    params: list = [user_id]
    where = "WHERE deleted = 0 AND (visibility = 'family' OR user_id = ?)"
    if q:
        where += " AND name LIKE ?"
        params.append(f"%{q}%")
    params.append(limit)
    cur = await db.execute(
        f"""SELECT id, name, relationship, visibility, user_id, preferences
            FROM people
            {where}
            ORDER BY lower(name), name
            LIMIT ?""",
        params,
    )
    rows = await cur.fetchall()
    people = []
    for r in rows:
        avatar = None
        try:
            pref = json.loads(r["preferences"]) if r["preferences"] else None
            if isinstance(pref, dict):
                avatar = pref.get("avatar_url")
        except (json.JSONDecodeError, TypeError):
            pass
        people.append({
            "id": r["id"],
            "name": r["name"],
            "relationship": r["relationship"],
            "avatar_url": avatar,
            "visibility": r["visibility"],
        })
    return {"people": people, "count": len(people)}


@router.get("/export")
async def export_user_memories(
    user_id: Optional[str] = Query(
        None,
        description="User to export. Defaults to the caller. Admins may specify any user.",
    ),
    admin: dict = Depends(require_admin),
):
    """Full MemPalace dump for a user. Admin-only."""
    target = user_id or admin["user_id"]
    try:
        payload = await _svc().export_user(target)
    except MemoryServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return payload


@router.post("/users/{target_user}/forget")
async def forget_user(
    target_user: str,
    admin: dict = Depends(require_admin),
):
    """Right-to-be-forgotten: delete all MemPalace rows for a user.

    Audited to `mempalace_audit`. Idempotent — a second call returns
    `{removed: 0}`.
    """
    try:
        removed = await _svc().delete_user(target_user, actor=admin["user_id"])
    except MemoryServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"user_id": target_user, "removed": removed}


async def _registered_account(user_id: str) -> bool:
    """True when Zoe Auth holds an account with this id.

    Reads ``auth_users`` (zoe-auth's account store, same Postgres) through the
    shared pool. NOT zoe-data's ``users`` table: ``/api/chat`` inserts a row there
    for every id it sees, so a harness demo user is in ``users`` after its first
    turn. Raises on any lookup failure — callers refuse (fail closed).
    """
    from db_pool import get_db_ctx  # deferred: keeps this module importable in unit tests

    async with get_db_ctx() as db:
        cur = await db.execute("SELECT 1 FROM auth_users WHERE user_id = ? LIMIT 1", (user_id,))
        return (await cur.fetchone()) is not None


@router.get("/capture-status")
async def memory_capture_status(request: Request, user_id: str = Query(..., min_length=1)):
    """Internal-token only: per-user counters of the post-turn memory capture
    (``memory_capture_stats``: started / completed / failed / in_flight /
    last_completed_at). No memory content. Lets a harness WAIT for a turn's
    background extraction + digest to finish instead of sleeping — the chat
    route schedules it with ``ensure_future``, so the HTTP turn proves nothing.
    Missing header 401, wrong/unprovisioned token 403.
    """
    from memory_capture_stats import snapshot

    if not request.headers.get("X-Internal-Token"):
        raise HTTPException(status_code=401, detail="capture-status requires X-Internal-Token")
    if not _has_valid_internal_token(request):
        raise HTTPException(status_code=403, detail="capture-status: invalid X-Internal-Token")
    return {"user_id": user_id, **snapshot(user_id)}


@router.get("/user-model")
async def memory_user_model(request: Request, user_id: str = Query(..., min_length=1)):
    """The Flue brain's always-present user-model block
    (``user_portrait.load_user_model_block``). Portrait prose is personal, so
    TOKEN only, as capture-status: missing header 401, wrong/unprovisioned 403."""
    if not request.headers.get("X-Internal-Token"):
        raise HTTPException(status_code=401, detail="user-model requires X-Internal-Token")
    if not _has_valid_internal_token(request):
        raise HTTPException(status_code=403, detail="user-model: invalid X-Internal-Token")
    from user_portrait import load_user_model_block

    return await load_user_model_block(user_id)


@router.post("/users/{target_user}/forget-synthetic")
async def forget_synthetic_user(target_user: str, request: Request):
    """Hard-forget a SYNTHETIC test user's memory rows — harness teardown.

    Same deletion as the admin ``/forget`` (``MemoryService.delete_user``: every
    row owned by the id, any status, plus its audit rows; idempotent, a second
    call returns ``removed: 0``). Differences, all fail-closed:
      * auth is the internal token ONLY (``X-Internal-Token`` == ``ZOE_INTERNAL_TOKEN``)
        — loopback alone is not enough; missing header 401, wrong/unprovisioned 403;
      * the id must pass ``user_filters.synthetic_forget_refusal`` — harness-minted
        ``demo_<tag>_<hex>`` / ``test_<tag>_<hex>`` only, not allowlisted, never a
        guest sentinel — else 403 with the reason;
      * the id must NOT be a registered Zoe Auth account (``auth_users`` — the
        account store, not zoe-data's ``users`` mirror, which ``/api/chat`` fills
        for every id it sees): a registered id is 403 ``refused_registered``, and
        a lookup that FAILS is 409 ``refused_unverified`` — fail closed.
    Every call that reaches the id check logs one ``MEMORY_FORGET_SYNTHETIC`` line
    naming the id and the outcome.
    """
    from user_filters import synthetic_forget_refusal

    if not request.headers.get("X-Internal-Token"):
        raise HTTPException(status_code=401, detail="forget-synthetic requires X-Internal-Token")
    if not _has_valid_internal_token(request):
        raise HTTPException(
            status_code=403,
            detail="forget-synthetic: invalid X-Internal-Token (or ZOE_INTERNAL_TOKEN unprovisioned)",
        )
    refusal = synthetic_forget_refusal(target_user)
    if refusal:
        logger.warning("MEMORY_FORGET_SYNTHETIC refused user=%r reason=%s", target_user, refusal)
        raise HTTPException(status_code=403, detail=f"forget-synthetic refused: {refusal}")
    try:
        registered = await _registered_account(target_user)
    except Exception as exc:  # noqa: BLE001 — any lookup failure refuses (fail closed)
        logger.error("MEMORY_FORGET_SYNTHETIC user=%s outcome=refused_unverified error=%s",
                     target_user, exc)
        raise HTTPException(
            status_code=409,
            detail="forget-synthetic refused: could not verify the id is not a registered account",
        )
    if registered:
        logger.warning("MEMORY_FORGET_SYNTHETIC user=%s outcome=refused_registered", target_user)
        raise HTTPException(
            status_code=403,
            detail="forget-synthetic refused: id belongs to a registered account — "
                   "real users need the admin forget",
        )
    try:
        removed = await _svc().delete_user(target_user, actor="internal:forget-synthetic")
    except MemoryServiceError as exc:
        # delete_user removes memory rows before audit rows, so a raise here can
        # be a PARTIAL delete — it must be visible in the audit log, not just a 400.
        logger.error("MEMORY_FORGET_SYNTHETIC user=%s outcome=error (deletion may be partial) "
                     "error=%s", target_user, exc)
        raise HTTPException(status_code=400, detail=str(exc))
    logger.warning("MEMORY_FORGET_SYNTHETIC user=%s removed=%d outcome=ok", target_user, removed)
    return {"user_id": target_user, "removed": removed, "mode": "synthetic"}


@router.post("/link-preview")
async def link_preview(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Best-effort title/content preview by substring match over notes.

    Still pulls from the `notes` table because notes haven't migrated yet;
    it's a read-only convenience endpoint used by the journal UI when the
    user types a URL or keyword.
    """
    await require_feature_access(db, user, feature="memories", action="read")
    query = (payload or {}).get("query") or (payload or {}).get("url") or ""
    if not query:
        return {"preview": [], "count": 0}
    query = str(query)
    if len(query) > _MAX_LIKE_QUERY_LENGTH:
        raise HTTPException(status_code=422, detail="query is too long")
    pattern = f"%{query}%"
    cur = await db.execute(
        """SELECT id, title, content, category, updated_at
           FROM notes
           WHERE deleted = 0 AND (visibility = 'family' OR user_id = ?)
             AND (title LIKE ? OR content LIKE ?)
           ORDER BY updated_at DESC
           LIMIT 10""",
        (user["user_id"], pattern, pattern),
    )
    rows = await cur.fetchall()
    return {
        "preview": [dict(r) for r in rows],
        "count": len(rows),
    }


# ─── Opt-out preference ─────────────────────────────────────────────────


@router.get("/opt-out")
async def get_memory_opt_out(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return the caller's memory opt-out flag. Default False."""
    await require_feature_access(db, user, feature="memories", action="read")
    from user_prefs import is_memory_opted_out
    flag = await is_memory_opted_out(user["user_id"], db=db)
    return {"user_id": user["user_id"], "memory_opt_out": flag}


@router.put("/opt-out")
async def set_memory_opt_out(
    payload: dict,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Toggle the caller's memory opt-out flag.

    When set, the post-turn extractor silently drops new chat-derived
    memories for this user (PII scrubber / idempotency logic stays intact
    for explicit ingest paths so the right-to-be-forgotten flow still
    works). Flipping the flag does NOT purge past memories — use
    `POST /api/users/{id}/forget` for that.
    """
    await require_feature_access(db, user, feature="memories", action="write")
    value = bool((payload or {}).get("memory_opt_out"))
    from user_prefs import KEY_MEMORY_OPT_OUT, set_pref
    await set_pref(user["user_id"], KEY_MEMORY_OPT_OUT, value, db=db)
    return {"user_id": user["user_id"], "memory_opt_out": value}
