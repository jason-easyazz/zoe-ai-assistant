"""Household recall on every turn (``ZOE_RECALL_EVERY_TURN`` = off | shadow | on, default off). Doc: docs/knowledge/recall-every-turn.md.

Both recall injectors on the Flue lane are trigger-gated, and real household speech matches neither on ~94% of brain turns
(docs/knowledge/recall-not-firing-2026-10-29.md). On a household turn NO other memory block claimed, this adds the member's own durable
facts that the turn's words touch, as one small block after the user's words. Every rule is reused, none is decided here:

* **Who**: household traffic (``traffic_class``), an identified member, a voice the speaker gate did not reject (an unverified voice
  is a guest), not an off-record turn. Anything else: no read at all.
* **Which rows**: ``load_durable_for_hop`` (the member's OWN approved durable rows, authority >= owner-derived: inferred never), minus
  every private class (``provenance_answers``) and off-the-wall restraint class (``panel_review_cards.OFF_THE_WALL``), every muted
  topic, every forgotten / tombstoned name (fails closed); a row about another person only when the owner's words pull it.
* **Relevance**: the recall gate's own lexical scoring (``recall_gate.derive`` / ``row_feat`` / ``_score`` and its threshold: names,
  rare keys, clock times, routine x plan) - no model call, no embedding. Nothing passes: no block (the common case).
* **Budget**: ``MAX_ROWS`` rows / ``BUDGET_TOKENS`` estimated tokens, each row cut at ``ROW_CHARS``.
* **Cost**: the pool's features are cached per member (``_Index``); per turn = set intersections + one by-id RE-READ of the picks
  (``MemoryService.recheck_durable``), so a correction or a forget is honoured on the very next turn.

Modes: ``off`` = nothing runs, today's bytes. ``shadow`` = measured in the background, nothing injected. ``on`` = injected (bounded
by ``POOL_TIMEOUT_S``) where the hop block rides, behind the system prompt, tools and history: the prompt-cache prefix and the wake
warm (``prefix_key``) are untouched; the ``[MEMORY CONTEXT`` delimiters let the sidecar elide it from older turns. In shadow and on,
every eligible household brain turn writes one ``RECALL_EVERY_TURN`` coverage line (rows the words touch, which other block fired as
``claimed``, ``injected``) - ids, counts and sizes only, never text. Never raises: fail-closed to "no block".
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

FLAG = "ZOE_RECALL_EVERY_TURN"
MODES = ("off", "shadow", "on")

#: rows in one block. Household brain turns re-prefill a p50 of ~220 tokens (FLUE_PROMPT_CACHE, 2026-11); the block is held to well
#: under that so a turn the gate passes costs at most ~130 tokens of extra prefill, and the common turn (nothing passes) costs none.
MAX_ROWS = 3
#: the rows' estimated tokens (``recall_gate.est_tokens``: chars/4 + 6 per bullet); the opener and close add ~35 more
BUDGET_TOKENS = 90
#: a row longer than this is cut at a word boundary
ROW_CHARS = 160
#: a cold index build / the by-id recheck: on the voice path the turn never waits longer than this for either
POOL_TIMEOUT_S = 0.25
#: a longer message (a paste) is not scanned
MAX_MESSAGE_CHARS = 512

BLOCK_OPEN = (
    "[MEMORY CONTEXT — things this user told you before that bear on what they just said; "
    "use them only where they help, do not list them, do not mention this block]"
)
BLOCK_CLOSE = "[END MEMORY CONTEXT]"

_ON = frozenset({"on", "1", "true", "yes", "enforce"})
_PENDING: set = set()


def mode() -> str:
    """``ZOE_RECALL_EVERY_TURN`` - ``off`` (default) | ``shadow`` | ``on``; read per call, so a flip needs no code change."""
    from typed_env import env_str

    raw = env_str("ZOE_RECALL_EVERY_TURN", "off").lower()  # literal on purpose: tools/audit/flag_inventory.py greps it
    if raw in _ON:
        return "on"
    return "shadow" if raw == "shadow" else "off"


@dataclass(frozen=True)
class Selection:
    ids: tuple = ()
    texts: tuple = ()
    tokens: int = 0
    pool: int = 0
    skipped: str = ""      # "" | not_household | guest | unverified | off_record | empty | timeout | error

    def block(self) -> str:
        if not self.texts:
            return ""
        lines = [BLOCK_OPEN]
        for rid, text in zip(self.ids, self.texts):
            safe = text.replace(BLOCK_CLOSE, "[​" + BLOCK_CLOSE[1:])
            lines.append(f"- {safe} [mem:{str(rid)[:8]}]")
        lines.append(BLOCK_CLOSE)
        return "\n".join(lines)


def _cut(text: str) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= ROW_CHARS:
        return t
    return t[:ROW_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def harness_admitted() -> bool:
    """``ZOE_RECALL_EVERY_TURN_HARNESS`` (default off): ALSO serve the Samantha-bar / day-sim harness ids (``demo_*``, traffic class
    ``harness``), so the bar can measure the flag off vs on. Never a guest, a probe or a CI id; never set it outside a measured run."""
    from typed_env import env_bool

    return env_bool("ZOE_RECALL_EVERY_TURN_HARNESS", False)


def eligible(user_id: str, message: str) -> str:
    """'' when this turn may carry the block, else the reason it may not. Pure but for the context variables it reads."""
    import traffic_class

    uid = (user_id or "").strip()
    if not uid or not (message or "").strip() or len(message) > MAX_MESSAGE_CHARS:
        return "empty"
    from memory_service import is_guest_memory_user

    if is_guest_memory_user(uid):
        return "guest"
    forced = traffic_class.class_for_user(uid)
    household = traffic_class.current() == traffic_class.HOUSEHOLD and not forced
    if not household and not (harness_admitted() and forced == traffic_class.HARNESS):
        return "not_household"
    import restraint

    if restraint.current_verdict() is False:
        return "unverified"
    import memory_provenance

    if memory_provenance.is_off_record(uid, message):
        return "off_record"
    return ""


def _speakable(ref: Any, turn: Any, mutes: list) -> bool:
    """A row this UNPROMPTED surface may carry. Never a private class (``provenance_answers``: health, money, mood, private) or a
    restraint class kept off the wall (``panel_review_cards.OFF_THE_WALL``: health, money, grief, family_conflict, affect), never a
    muted topic; any other restraint class (``other_member``: a row about another person) only on the terms the recall packet uses
    in enforce - the owner's words pull it. Fails closed."""
    try:
        import panel_review_cards
        import provenance_answers
        import restraint

        meta, text = (getattr(ref, "metadata", None) or {}), str(getattr(ref, "text", "") or "")
        if provenance_answers.sensitive_classes(text, meta):
            return False
        classes = restraint.row_classes(meta, text)
        if set(classes) & panel_review_cards.OFF_THE_WALL:
            return False
        source = f"memory:{ref.id}"
        if restraint.muted(text, source, mutes):
            return False
        return not classes or restraint.decide(text, classes, turn, mutes, surface="packet", source_ref=source).allow
    except Exception:  # noqa: BLE001
        return False


async def _forgotten(user_id: str, text: str) -> bool:
    """Does ``text`` name something the member asked Zoe to forget? Fails CLOSED (an error hides the row)."""
    try:
        import memory_forgotten
        from memory_tombstones import matching_tombstone

        return bool(matching_tombstone(user_id, text) or await memory_forgotten.matches(user_id, text))
    except Exception:  # noqa: BLE001
        return True


# ── the member's gate index (cached) ────────────────────────────────────────────────────────────
#
# Reading and featurising the whole durable pool is one Chroma get plus a regex pass per row: ~100 ms at 400 rows, ~250 ms at
# 1,000 on the Orin (scripts/perf/recall_every_turn_replay.py) - over the voice budget. So the pool's gate features are cached per
# member (``INDEX_TTL_S``, refreshed in the background, stale-while-revalidate) and the per-turn work is: score the cached rows
# against the turn (set intersections), then RE-READ the few picks fresh by id (``MemoryService.recheck_durable``: the same rules,
# now). A row superseded, retired or deleted since the index was built fails the recheck and never enters; a row written since may be
# missed until the next refresh - the cache can only ever omit, never resurrect.

INDEX_TTL_S = 120.0
INDEX_MAX_USERS = 16


@dataclass
class _Index:
    built: float
    rows: list            # (Cand, Feat), the member's durable pool
    df: dict              # key -> how many of the member's rows carry it (rarity)


_INDEX: "dict[str, _Index]" = {}
_BUILDS: "dict[str, asyncio.Task]" = {}


def _reset_state() -> None:
    _INDEX.clear()
    _BUILDS.clear()


def _featurise(pool: list) -> "tuple[list, dict]":
    """(Cand, Feat) per row and the key rarity table. Pure CPU (a regex pass per unstamped row): run in a worker thread so a
    refresh never stalls the event loop that is streaming a reply."""
    import recall_gate

    rows, df = [], {}
    for ref in pool:
        c = recall_gate._cand(ref, "pool")
        f = recall_gate.row_feat(c.meta, c.text)
        rows.append((c, f))
        for k in set(f.keys):
            df[k] = df.get(k, 0) + 1
    return rows, df


async def _build(uid: str, svc: Any) -> _Index:
    pool = await svc.load_durable_for_hop(uid)
    rows, df = await asyncio.to_thread(_featurise, pool)
    ix = _Index(time.monotonic(), rows, df)
    _INDEX.pop(uid, None)
    _INDEX[uid] = ix
    while len(_INDEX) > INDEX_MAX_USERS:
        _INDEX.pop(next(iter(_INDEX)))
    return ix


def _build_task(uid: str, svc: Any) -> "asyncio.Task":
    loop = asyncio.get_running_loop()
    task = _BUILDS.get(uid)
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(_build(uid, svc))
        _BUILDS[uid] = task
        task.add_done_callback(lambda t, u=uid: _BUILDS.pop(u, None) if _BUILDS.get(u) is t else None)
    return task


def prewarm(user_id: str) -> None:
    """Start building ``user_id``'s index in the background (the wake warm calls this for the panel's likely speaker), so the first
    turn after a restart does not wait. A no-op when the flag is off or the id is not a member. Never raises."""
    try:
        uid = (user_id or "").strip()
        if mode() == "off" or not uid:
            return
        from memory_service import get_memory_service, is_guest_memory_user
        import traffic_class

        if is_guest_memory_user(uid) or traffic_class.class_for_user(uid):
            return
        if uid not in _INDEX:
            _build_task(uid, get_memory_service())
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_every_turn: prewarm skipped (%s)", type(exc).__name__)


async def _index(uid: str, svc: Any) -> Optional[_Index]:
    """The member's index: fresh -> as is; stale -> as is, with a background refresh; none -> wait for the build at most
    ``POOL_TIMEOUT_S`` (the build carries on for the next turn). None = not ready."""
    ix = _INDEX.get(uid)
    if ix is not None and time.monotonic() - ix.built <= INDEX_TTL_S:
        return ix
    task = _build_task(uid, svc)
    if ix is not None:
        return ix
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=POOL_TIMEOUT_S)
    except asyncio.TimeoutError:
        return None


def _ranked(ix: _Index, message: str) -> list:
    """The member's rows the owner's words TRIGGER (``recall_gate``'s lexical signals and threshold, scored against the rarity of
    keys across the member's whole pool), best first. No similarity, no filler, no session state."""
    import recall_gate

    cfg = recall_gate.PACKET
    msg = recall_gate.derive(message)
    keys, clocks = frozenset(msg.keys), set(msg.clocks)
    out = []
    for c, f in ix.rows:
        if not (keys.intersection(f.keys) or clocks.intersection(f.clocks) or (f.routine and msg.plan)):
            continue
        score, _why = recall_gate._score(c, f, msg, keys, ix.df, cfg)
        if score >= cfg.threshold:
            order = score + recall_gate.AUTH_BONUS.get(min(c.auth, 4), 0.0)
            out.append((order, c))
    out.sort(key=lambda t: (-t[0], -t[1].auth, -recall_gate._added(t[1].meta), t[1].id))
    return [c for _o, c in out]


async def select(user_id: str, message: str, *, svc: Any = None) -> Selection:
    """The rows this turn would carry. Never raises."""
    try:
        why = eligible(user_id, message)
        if why:
            return Selection(skipped=why)
        uid = user_id.strip()
        import recall_gate
        import restraint
        from memory_service import get_memory_service

        svc = svc or get_memory_service()
        ix = await _index(uid, svc)
        if ix is None:
            return Selection(skipped="timeout")
        picks = _ranked(ix, message)[: MAX_ROWS * 3]
        if not picks:
            return Selection(pool=len(ix.rows))
        fresh = {str(r.id): r for r in await asyncio.wait_for(svc.recheck_durable(uid, [c.id for c in picks]),
                                                                timeout=POOL_TIMEOUT_S)}
        mutes = await restraint.list_mutes(uid)
        turn = restraint.make_turn(message, verdict=restraint.current_verdict())   # THIS turn's words, never a noted older one
        ids, texts, tokens = [], [], 0
        for c in picks:
            ref = fresh.get(c.id)
            if ref is None or str(ref.text or "") != c.text:       # gone, retired or rewritten since the index was built
                continue
            if not _speakable(ref, turn, mutes) or await _forgotten(uid, c.text):
                continue
            shown = _cut(c.text)
            cost = recall_gate.est_tokens(shown)
            if ids and tokens + cost > BUDGET_TOKENS:
                continue
            ids.append(c.id)
            texts.append(shown)
            tokens += cost
            if len(ids) >= MAX_ROWS:
                break
        return Selection(tuple(ids), tuple(texts), tokens, len(ix.rows))
    except Exception as exc:  # noqa: BLE001 - recall must never break a turn (a failed recheck included: fail closed)
        logger.debug("recall_every_turn: selection failed (%s)", type(exc).__name__)
        return Selection(skipped="error")


def _log(user_id: str, sel: Selection, m: str, ms: float, claimed: str = "", injected: bool = False) -> None:
    """THE coverage line, one per household brain turn while the flag is not off: ``rows`` = the member's own stored facts this turn's
    words touch (relevant memory EXISTED), ``claimed`` = the other memory blocks that fired (recall floor, continuity, hop, ...),
    ``injected`` = this block rode. ``rows>0 claimed=-`` is the gap this path closes. Ids, counts and sizes only, never text."""
    if sel.skipped == "not_household":       # our own harness / probe / CI traffic is not household coverage
        return
    import recall_gate

    logger.info("RECALL_EVERY_TURN user=%s mode=%s rows=%d ids=%s tokens=%d pool=%d ms=%.1f claimed=%s injected=%d skipped=%s",
                user_id, m, len(sel.ids), ",".join(recall_gate.short_id(i) for i in sel.ids) or "-", sel.tokens, sel.pool, ms,
                claimed or "-", int(injected), sel.skipped or "-")


async def _shadow(user_id: str, message: str, svc: Any, m: str = "shadow", claimed: str = "") -> None:
    t0 = time.perf_counter()
    sel = await select(user_id, message, svc=svc)
    _log(user_id, sel, m, (time.perf_counter() - t0) * 1000.0, claimed)


def _spawn(coro: Any) -> None:
    try:
        task = asyncio.ensure_future(coro)
    except RuntimeError:
        coro.close()
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def drain() -> None:
    """Wait for the background shadow selections (tests and the offline measurement)."""
    while _PENDING:
        await asyncio.gather(*list(_PENDING), return_exceptions=True)


async def block_for(user_id: str, message: str, *, claimed_by: str = "", svc: Optional[Any] = None) -> str:
    """The turn's block ('' when off, in shadow, ineligible, or nothing passed the gate). ``claimed_by``: the other memory blocks
    this turn already carries (comma list) - then nothing is injected (one memory block per turn) and the selection only MEASURES,
    in the background, so the coverage line covers every household turn. Never raises."""
    try:
        m = mode()
        if m == "off":
            return ""
        if m == "shadow" or claimed_by:
            _spawn(_shadow(user_id, message, svc, m, claimed_by))
            return ""
        t0 = time.perf_counter()
        sel = await select(user_id, message, svc=svc)
        _log(user_id, sel, "on", (time.perf_counter() - t0) * 1000.0, injected=bool(sel.texts))
        if sel.texts:
            try:   # BM5: "why did you say that?" names the rows this reply stood on
                import memory_provenance

                memory_provenance.note_served(user_id, list(zip(sel.ids, sel.texts)))
            except Exception:  # noqa: BLE001
                pass
        return sel.block()
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_every_turn: block failed (%s)", type(exc).__name__)
        return ""
