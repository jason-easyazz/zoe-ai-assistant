"""Household recall on every turn (``ZOE_RECALL_EVERY_TURN`` = off | shadow | on, default off). Doc: docs/knowledge/recall-every-turn.md.

Both recall injectors on the Flue lane are trigger-gated, and real household speech matches neither on ~94% of brain turns
(docs/knowledge/recall-not-firing-2026-10-29.md). On a household turn NO other memory block claimed, this adds the member's own durable
facts that the turn's words touch, as one small block after the user's words. Every rule is reused, none is decided here:

* **Who**: household traffic (``traffic_class``), an identified member, on a spoken turn a speaker the gate CONFIRMED (no verdict is
  not a yes; an unverified voice is a guest), not an off-record turn. Anything else: no read at all.
* **Which rows**: ``load_durable_for_hop`` (the member's OWN approved durable rows, authority >= owner-derived: inferred never), minus
  every private class (``provenance_answers``) and off-the-wall restraint class (``panel_review_cards.OFF_THE_WALL``), every muted
  topic, every forgotten / tombstoned name (fails closed); a row about another person only when the owner's words pull it.
* **Relevance**: the recall gate's own lexical scoring (``recall_gate.derive`` / ``row_feat`` / ``_score`` and its threshold: names,
  rare keys, clock times, routine x plan) - no model call, no embedding. Nothing passes: no block (the common case).
* **Budget**: ``MAX_ROWS`` rows / ``BUDGET_TOKENS`` estimated tokens, each row cut at ``ROW_CHARS``.
* **Cost**: the pool's features are cached per member (``_Index``); per turn = set intersections + one by-id RE-READ of the picks
  (``MemoryService.recheck_durable``), so a correction or a forget is honoured on the very next turn.

* **Similar rows** (``ZOE_RECALL_SIMILAR`` = off | shadow | on, default off; docs/knowledge/recall-every-turn.md): the lexical
  gate needs a shared name, rare word or clock time, so oblique speech ("what should I get her") never meets the row it needs. With
  this on, a turn whose words trigger fewer than ``MAX_ROWS`` rows is topped up with the member's nearest durable rows by embedding
  similarity (the palace's own MiniLM, the cached pool, a floor of ``ZOE_RECALL_SIMILAR_FLOOR``). Same rows, same walls, same
  recheck, same budget; the block's opener then says the rows MAY relate. ``shadow`` computes and logs, injects nothing.

Modes: ``off`` = nothing runs, today's bytes. ``shadow`` = measured in the background, nothing injected. ``on`` = injected (a cold
index is never waited for) where the hop block rides, behind the system prompt, tools and history: the prompt-cache prefix and the wake
warm (``prefix_key``) are untouched; the ``[MEMORY CONTEXT`` delimiters let the sidecar elide it from older turns. In shadow and on,
every eligible household brain turn writes one ``RECALL_EVERY_TURN`` coverage line (rows the words touch, which other block fired as
``claimed``, ``injected``) - ids, counts and sizes only, never text. Never raises: fail-closed to "no block".
"""
from __future__ import annotations

import asyncio
import logging
import threading
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
#: the by-id recheck of the picks: on the voice path the turn never waits longer than this for it
POOL_TIMEOUT_S = 0.25
#: the similar arm: one MiniLM embed of the turn's words (padded to length: p50 ~4 ms on the Orin CPU, 2026-11-13; the palace's own
#: fixed 256-token padding was 41-165 ms), started early by ``begin``; a slower one is dropped
EMBED_TIMEOUT_S = 0.20
#: cosine floor for a similar row. Synthetic lab with the palace's MiniLM (labs/recall-match-lab, 2026-11-12): at 0.30 the needed
#: fact is found on 9 of 20 oblique turns (the lexical gate: 2 of 20) and a row is offered on 2 of 20 irrelevant turns; at 0.20 it
#: is 15 of 20 and 13 of 20. The default is the conservative end; the Samantha bar decides whether it may go lower.
SIMILAR_FLOOR = 0.30
#: unit vectors kept per row id (id -> (text hash, vector)); rebuilt indexes reuse them, so a refresh embeds only new rows
VEC_CACHE_MAX = 4000
#: a longer message (a paste) is not scanned
MAX_MESSAGE_CHARS = 512

BLOCK_OPEN = (
    "[MEMORY CONTEXT — things this user told you before that bear on what they just said; "
    "use them only where they help, do not list them, do not mention this block]"
)
BLOCK_OPEN_SIMILAR = (
    "[MEMORY CONTEXT — things this user told you before that MAY relate to what they just said; "
    "ignore any that do not, do not list them, do not mention this block]"
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


def similar_mode() -> str:
    """``ZOE_RECALL_SIMILAR`` - ``off`` (default) | ``shadow`` | ``on``. Only read while ``ZOE_RECALL_EVERY_TURN`` is not off."""
    from typed_env import env_str

    raw = env_str("ZOE_RECALL_SIMILAR", "off").lower()  # literal on purpose: tools/audit/flag_inventory.py greps it
    if raw in _ON:
        return "on"
    return "shadow" if raw == "shadow" else "off"


def similar_floor() -> float:
    from typed_env import env_float

    return min(0.95, max(0.0, env_float("ZOE_RECALL_SIMILAR_FLOOR", SIMILAR_FLOOR)))


@dataclass(frozen=True)
class Selection:
    ids: tuple = ()
    texts: tuple = ()
    tokens: int = 0
    pool: int = 0
    skipped: str = ""      # "" | not_household | guest | unverified | off_record | empty | timeout | error
    sim: int = 0           # similar rows above the floor this turn (candidates, before the walls); shadow counts them too
    sim_in: int = 0        # of ``ids``, how many came from similarity and not from the lexical gate
    sim_top: float = 0.0   # the best similar row's cosine

    def block(self) -> str:
        if not self.texts:
            return ""
        lines = [BLOCK_OPEN_SIMILAR if self.sim_in else BLOCK_OPEN]
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


def eligible(user_id: str, message: str, voice: bool = False) -> str:
    """'' when this turn may carry the block, else the reason it may not. ``voice``: a spoken turn, which needs the speaker gate's
    CONFIRMED verdict (voice is a password: no verdict is not a yes). Pure but for the context variables it reads."""
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

    verdict = restraint.current_verdict()
    if verdict is False or (voice and verdict is not True):
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

        return bool(matching_tombstone(user_id, text) or await memory_forgotten.matches(user_id, text, strict=True))
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
    vecs: Any = None      # one unit vector per row, aligned with ``rows`` (None: the similar arm is off or could not embed)


_INDEX: "dict[str, _Index]" = {}
_BUILDS: "dict[str, asyncio.Task]" = {}


_VECS: "dict[tuple, tuple[int, Any]]" = {}      # (member, row id) -> (text hash, unit vector): never shared across members
#: the last turn embed's future + a hash of its words (never the words): still running = the arm is busy (a timeout does not stop a
#: worker); the same words again = ``begin`` already started it, so the turn only waits for what is left of it
_QUERY_EMBEDS: dict = {"job": None, "key": None}


def _reset_state() -> None:
    _INDEX.clear()
    _BUILDS.clear()
    _VECS.clear()
    _QUERY_EMBEDS["job"] = None
    _QUERY_EMBEDS["key"] = None
    _FAST["ef"] = None
    _FAST["tok"] = None


_EMBED_LOCK = threading.Lock()
#: the palace EF this module's tokenizer was built for (rebuilt if the palace swaps its EF) and that tokenizer
_FAST: dict = {"ef": None, "tok": None}
#: sentence-transformers' max_seq_length for this MiniLM (the palace EF truncates at the same length)
MAX_TOKENS = 256


def _embed_dynamic(ef: Any, texts: list) -> Any:
    """The palace MiniLM's vectors (float32, not yet unit) with the batch padded to its LONGEST text, not to 256 tokens.

    Chroma's ``ONNXMiniLM_L6_V2`` pads every text to a fixed 256 tokens, so a 10-word turn runs ~25x the tokens it needs: 41-165 ms
    on the Orin CPU against ~4 ms padded to length (docs/knowledge/recall-every-turn.md). Mean pooling is attention-masked, so the
    pad tokens carry no weight and the vectors are the same (cosine 1.00000 against the fixed-pad vectors on the 100 lab texts).
    SAME session (``ef.model``) and same ``tokenizer.json`` as the palace; only a second tokenizer object with no fixed length, so
    the palace's own embeds are untouched. Raises on anything unexpected: the caller falls back to ``ef(texts)``."""
    import os

    import numpy as np

    if _FAST["ef"] is not ef:
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(os.path.join(ef.DOWNLOAD_PATH, ef.EXTRACTED_FOLDER_NAME, "tokenizer.json"))
        tok.enable_truncation(max_length=MAX_TOKENS)
        tok.enable_padding(pad_id=0, pad_token="[PAD]")
        _FAST["tok"], _FAST["ef"] = tok, ef
    enc = _FAST["tok"].encode_batch([str(t) for t in texts])
    ids = np.array([e.ids for e in enc], dtype=np.int64)
    mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
    out = ef.model.run(None, dict(input_ids=ids, attention_mask=mask, token_type_ids=np.zeros_like(ids)))[0]
    m = mask[..., None].astype(np.float32)
    return (out * m).sum(1) / np.clip(m.sum(1), 1e-9, None)


def _embed_raw(texts: list) -> Any:
    import numpy as np
    from memory_service import _drawers_embedding_function

    ef = _drawers_embedding_function()
    try:
        v = np.asarray(_embed_dynamic(ef, texts), dtype=np.float32)
    except Exception as exc:  # noqa: BLE001 - any surprise (another EF, a missing file): the palace's own call, slower but the same
        logger.debug("recall_every_turn: dynamic-pad embed unavailable (%s)", type(exc).__name__)
        v = np.asarray(ef(list(texts)), dtype=np.float32)
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0.0] = 1.0
    return v / n


def _embed(texts: list) -> Any:
    """Unit vectors (float32, one per text) from the palace's own embedding function (one MiniLM ONNX session per process:
    ``memory_service._drawers_embedding_function``), so a row and a turn are compared in the space the rows are stored in.
    ONE embed at a time in this module, whoever asks (a member's index build, another member's, a turn): a turn that arrives
    behind a build simply waits out its 200 ms and carries no similar rows, rather than two runs sharing the session and the CPU."""
    with _EMBED_LOCK:
        return _embed_raw(texts)


def _row_vectors(uid: str, rows: list) -> Any:
    """The pool's vectors, embedding only the rows not seen before for THIS member (by row id and text). Each row's vector is taken
    from this call's own work or from a cache entry whose text hash matches, never re-read by key later, so a concurrent build for
    another member (or a rewritten row) cannot hand this index a vector of some other text. None on any failure: no similar arm."""
    try:
        import numpy as np

        if not rows:
            return None
        vecs, todo = {}, []
        for c, _f in rows:
            hit = _VECS.get((uid, c.id))
            if hit is not None and hit[0] == hash(c.text):
                vecs[c.id] = hit[1]
            else:
                todo.append((c.id, c.text))
        for i in range(0, len(todo), 64):
            part = todo[i:i + 64]
            for (rid, text), vec in zip(part, _embed([t for _i, t in part])):
                vecs[rid] = vec
                _VECS[(uid, rid)] = (hash(text), vec)
        if len(_VECS) > VEC_CACHE_MAX:
            keep = {(uid, c.id) for c, _f in rows}
            for k in [k for k in list(_VECS) if k not in keep][: len(_VECS) - VEC_CACHE_MAX]:
                _VECS.pop(k, None)
        return np.stack([vecs[c.id] for c, _f in rows])
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_every_turn: row vectors unavailable (%s)", type(exc).__name__)
        return None


def _featurise(pool: list, uid: str = "") -> "tuple[list, dict, Any]":
    """(Cand, Feat) per row, the key rarity table, and (similar arm on) the rows' vectors. Pure CPU (a regex pass per unstamped row): run in a worker thread so a
    refresh never stalls the event loop that is streaming a reply."""
    import recall_gate

    rows, df = [], {}
    for ref in pool:
        c = recall_gate._cand(ref, "pool")
        f = recall_gate.row_feat(c.meta, c.text)
        rows.append((c, f))
        for k in set(f.keys):
            df[k] = df.get(k, 0) + 1
    return rows, df, (_row_vectors(uid, rows) if similar_mode() != "off" else None)


async def _build(uid: str, svc: Any) -> _Index:
    pool = await svc.load_durable_for_hop(uid)
    rows, df, vecs = await asyncio.to_thread(_featurise, pool, uid)
    ix = _Index(time.monotonic(), rows, df, vecs)
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
    """The member's index: fresh -> as is; stale -> as is, with a background refresh; none -> None at once, with the build started
    in the background (the turn never waits for a whole-pool read: that turn simply carries no block; the wake warm prewarms)."""
    ix = _INDEX.get(uid)
    if ix is not None and time.monotonic() - ix.built <= INDEX_TTL_S:
        return ix
    _build_task(uid, svc)
    return ix


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


def _rank_vec(vecs: Any, qvec: Any, k: int) -> list:
    """(row index, cosine) for the ``k`` rows nearest the turn's vector, best first (both unit vectors)."""
    import numpy as np

    sims = vecs @ qvec
    top = np.argsort(-sims)[:k]
    return [(int(j), float(sims[j])) for j in top]


def _turn_job(message: str) -> "Optional[asyncio.Future]":
    """The future of THIS turn's embed (shape (1, d)): the one ``begin`` already started for the same words, else a new one, else
    None when an earlier turn's embed is still running (a timeout cannot stop a worker thread: never start another behind it).
    Must be called on the running loop."""
    key = hash(message)
    prev = _QUERY_EMBEDS["job"]
    if prev is not None and _QUERY_EMBEDS["key"] == key and not prev.cancelled():
        return prev
    if prev is not None and not prev.done():
        return None
    # the FUTURE is the busy mark: done when the worker returns, raises, or is cancelled before it ever starts, so nothing can
    # leave the arm switched off. Callers shield it: a turn's timeout (or cancellation) must not cancel the mark itself.
    job = _QUERY_EMBEDS["job"] = asyncio.get_running_loop().run_in_executor(None, _embed, [message])
    _QUERY_EMBEDS["key"] = key
    job.add_done_callback(lambda f: f.cancelled() or f.exception())      # a late failure is consumed, never "never retrieved"
    return job


def begin(user_id: str, message: str, *, voice: bool = False) -> None:
    """Start this turn's embed NOW (the similar arm only), so it runs beside the routing / packet / block work that precedes
    ``block_for`` and the brain call waits only for what is left of it, not the whole ~4 ms (~40-160 ms before the dynamic-pad
    embed). Call it where the turn's words are final, on the running loop. A no-op unless both flags are on, the turn is eligible
    and the member's index already holds vectors (nothing to compare against = nothing to embed). Never raises, never waits."""
    try:
        if mode() == "off" or similar_mode() == "off" or eligible(user_id, message, voice):
            return
        ix = _INDEX.get((user_id or "").strip())
        if ix is None or ix.vecs is None or not ix.rows:
            return
        _turn_job(message)
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_every_turn: begin skipped (%s)", type(exc).__name__)


async def _similar(ix: _Index, message: str, taken: set) -> list:
    """(cosine, Cand) for the member's rows nearest the turn's words, best first, at or above the floor, not already ``taken``.
    [] when the index has no vectors, the embed is slow or anything fails: the similar arm can only ever add nothing. One turn
    embed at a time: a timeout cannot stop its worker thread, so while one is still running the next turn skips the arm instead
    of starting another (work never piles up behind a slow model)."""
    try:
        if ix.vecs is None or not ix.rows:
            return []
        job = _turn_job(message)           # usually already running: ``begin`` started it when the words were final
        if job is None:
            return []
        qvec = (await asyncio.wait_for(asyncio.shield(job), timeout=EMBED_TIMEOUT_S))[0]
        near = _rank_vec(ix.vecs, qvec, MAX_ROWS * 3)      # one matrix-vector product: microseconds, no thread hop
        floor = similar_floor()
        return [(sim, ix.rows[j][0]) for j, sim in near if sim >= floor and ix.rows[j][0].id not in taken]
    except Exception as exc:  # noqa: BLE001 - a timeout included
        logger.debug("recall_every_turn: similar arm skipped (%s)", type(exc).__name__)
        return []


async def select(user_id: str, message: str, *, voice: bool = False, svc: Any = None) -> Selection:
    """The rows this turn would carry. Never raises."""
    try:
        why = eligible(user_id, message, voice)
        if why:
            return Selection(skipped=why)
        uid = user_id.strip()
        import recall_gate
        import restraint
        from memory_service import get_memory_service

        svc = svc or get_memory_service()
        ix = await _index(uid, svc)
        if ix is None:
            return Selection(skipped="warming")
        picks = _ranked(ix, message)[: MAX_ROWS * 3]
        smode, near = similar_mode(), []
        if smode != "off" and len(picks) < MAX_ROWS:      # the words triggered too little: look by meaning
            near = await _similar(ix, message, {c.id for c in picks})
        sim, sim_top = len(near), (near[0][0] if near else 0.0)
        from_sim = {c.id for _s, c in near} if smode == "on" else set()
        if smode == "on":
            picks = picks + [c for _s, c in near]         # lexical rows first: they keep the budget
        if not picks:
            return Selection(pool=len(ix.rows), sim=sim, sim_top=sim_top)
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
            if tokens + cost > BUDGET_TOKENS:
                continue
            ids.append(c.id)
            texts.append(shown)
            tokens += cost
            if len(ids) >= MAX_ROWS:
                break
        return Selection(tuple(ids), tuple(texts), tokens, len(ix.rows), sim=sim, sim_in=sum(1 for i in ids if i in from_sim),
                         sim_top=sim_top)
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

    logger.info("RECALL_EVERY_TURN user=%s mode=%s rows=%d ids=%s tokens=%d pool=%d ms=%.1f claimed=%s injected=%d skipped=%s "
                "similar=%s sim=%d sim_in=%d sim_top=%.2f",
                user_id, m, len(sel.ids), ",".join(recall_gate.short_id(i) for i in sel.ids) or "-", sel.tokens, sel.pool, ms,
                claimed or "-", int(injected), sel.skipped or "-", similar_mode(), sel.sim, sel.sim_in, sel.sim_top)


async def _shadow(user_id: str, message: str, svc: Any, m: str = "shadow", claimed: str = "", voice: bool = False) -> None:
    t0 = time.perf_counter()
    sel = await select(user_id, message, voice=voice, svc=svc)
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


async def block_for(user_id: str, message: str, *, claimed_by: str = "", voice: bool = False, svc: Optional[Any] = None) -> str:
    """The turn's block ('' when off, in shadow, ineligible, or nothing passed the gate). ``claimed_by``: the other memory blocks
    this turn already carries (comma list) - then nothing is injected (one memory block per turn) and the selection only MEASURES,
    in the background, so the coverage line covers every household turn. Never raises."""
    try:
        m = mode()
        if m == "off":
            return ""
        if m == "shadow" or claimed_by:
            _spawn(_shadow(user_id, message, svc, m, claimed_by, voice))
            return ""
        t0 = time.perf_counter()
        sel = await select(user_id, message, voice=voice, svc=svc)
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
