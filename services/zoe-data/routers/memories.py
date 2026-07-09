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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from auth import get_current_user, require_admin, require_internal_token
from database import get_db
from guest_policy import require_feature_access
from memory_service import (
    MemoryRef,
    MemoryService,
    MemoryServiceError,
    get_memory_service,
)
from models import MemoryProposalCreate, MemoryReviewBody

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
        )
    except MemoryServiceError as exc:
        # ValueErrors from bad input become 400, missing-row becomes 404.
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
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
    """
    if boost_emotional and facts:
        facts = sorted(facts, key=_emotional_intensity, reverse=True)
    seen: set[str] = set()
    kept_tokens: list[frozenset] = []
    kept_ts: list[float] = []
    lines: list[str] = []
    refs: list[dict[str, Any]] = []

    def _consider(ref: MemoryRef, *, from_search: bool) -> None:
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
        lines.append(f"- {prefix}{text[:200]} {cite}")
        refs.append(
            {
                "id": ref.id,
                "memory_type": meta.get("memory_type", "fact"),
                "status": status,
                "from_search": from_search,
            }
        )

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
    return {"packet": "## What I know about you\n" + "\n".join(lines), "refs": refs, "count": len(refs)}


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


@router.get("/for-prompt")
async def memory_for_prompt(
    user_id: str = Query(..., min_length=1),
    message: str = Query(
        "",
        max_length=_MAX_PROMPT_MESSAGE_LENGTH,
        description="Current user message, for relevance ranking",
    ),
    limit: int = Query(_PROMPT_PACKET_MAX_FACTS, ge=1, le=40),
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
    # One metadata read. On an emotional turn we scan wider (_EMO_PIN_SCAN) so a
    # crowded-out emotional row is visible to the pin below; the generic packet
    # still uses only the first `limit` rows (load_for_prompt returns a stable
    # prefix, so this slice == the narrow read).
    scan = _EMO_PIN_SCAN if emo_turn else limit
    all_rows = await svc.load_for_prompt(user_id, limit=scan)
    facts = all_rows[:limit]
    hits: list[MemoryRef] = []
    needs_search = _message_needs_memory(message) or emo_turn
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
    result = _build_memory_prompt_packet(
        facts, hits, max_facts=limit, boost_emotional=emo_turn
    )
    result["user_scoped"] = True

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
    return result


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
            ORDER BY name COLLATE NOCASE
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
    flag = await is_memory_opted_out(user["user_id"])
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
    await set_pref(user["user_id"], KEY_MEMORY_OPT_OUT, value)
    return {"user_id": user["user_id"], "memory_opt_out": value}
