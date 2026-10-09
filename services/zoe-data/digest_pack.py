"""The nightly digest's PACK STEP: read the whole day, chunk by chunk, instead of the first 3,000 characters.

Why (docs/research/night-mind-2026-10-09.md, "The current nightly pass drops most of a busy day"): ``memory_digest`` cut the
transcript to 3,000 characters before the model saw it (fact extractor and emotional pass), read at most 200 turns, and the
open-loop pass read at most 50 turns with a 3,000-character budget. On a busy day ~70 % of the owner's words never reached a model
at all, and the model-call timeouts (45 s) were shorter than a 500-token reply at the speeds they had been sized for.

This module is the shared first stage of a map -> reduce:

* PACK (here, code): the day's turns are cut into chunks that FIT THE SLOT with the prompt overhead counted. The budget is the
  night mind's (``night_mind.chunk_budget``, PR #1930): 2,400 turn-tokens at the 8,192 slot, proportionally more at a bigger slot,
  never more than what is left of the slot after the instructions, the reply cap and a margin. The estimate, the 650 tok/s prefill
  figure and the timeout formula are the night mind's too (one definition, so the two cannot drift: when #1930 lands its copies
  become imports of these).
* MAP (``memory_digest``): one model call per chunk, the unchanged prompt of the pass over that chunk's turns.
* REDUCE (``memory_digest``, code): the per-chunk answers are merged with the EXISTING dedup (``memory_overlap.dedup_verdict`` for
  facts and moments, ``_loop_is_dup`` for loops). The observation gate (``memory_authority.check_observation``) and every write
  path downstream are untouched: a fact still needs the owner's verbatim words, and those are checked against the WHOLE day.

BOUNDED: at most ``max_chunks`` (default 5) model calls per pass per member-night. A day that needs more keeps its highest-signal turns (code
scoring; newest wins ties) and counts what it skipped. A pass costs at most 5 calls, so a member-night is at most 15 map calls
(facts + emotional moments + open loops) however long the day is; ``ZOE_DIGEST_MAX_FACTS`` (default 40) bounds the per-fact
contradiction checks that follow.

``ZOE_DIGEST_CHUNKED`` (default ON; ``0/false/no/off`` = the legacy truncation: first 3,000 characters, 200 / 50 turns, fixed timeouts).

Every run states what it read: ``DIGEST_COVERAGE turns_total= turns_read= chunks= calls= ...`` (counts only, logger
``memory_digest.open_loops`` - the counts-only logger the dreaming runner already prints).
"""
from __future__ import annotations

import contextvars
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from typed_env import env_bool, env_float, env_int, env_str

ENV = "ZOE_DIGEST_CHUNKED"
MAX_CHUNKS_ENV = "ZOE_DIGEST_MAX_CHUNKS"
MAX_FACTS_ENV = "ZOE_DIGEST_MAX_FACTS"
CHUNK_TOKENS_ENV = "ZOE_DIGEST_CHUNK_TOKENS"
DECODE_ENV = "ZOE_DIGEST_DECODE_TOK_S"
PREFILL_ENV = "ZOE_DIGEST_PREFILL_TOK_S"
SLOT_ENV = "ZOE_BRAIN_SLOT_TOKENS"            # the slot every digest pass talks to (memory_digest already reads it for the synthesis budget)

BASE_CTX = 8192
BASE_CHUNK_TOKENS = 2400                       # owner's words per map call at the 8k slot (night_mind.BASE_CHUNK_TOKENS)
PREFILL_TOK_S = 650.0                          # night_mind.PREFILL_TOK_S (the mind-layer's derived prefill rate)
FLOOR_S = 30.0                                 # night_mind.TIMEOUT_FLOOR_S
SLACK_S = 20.0                                 # night_mind.timeout_for's slack: a busy slot queues the request, the timeout bounds the wait
MARGIN_TOKENS = 300                            # night_mind.Config.chunk_budget's margin against the estimator and the chat template
MIN_BUDGET = 120


def enabled() -> bool:
    """Is the chunked pack step ON? Default yes; ``0/false/no/off`` is the legacy truncation. Read per call."""
    return env_bool("ZOE_DIGEST_CHUNKED", True) if env_str("ZOE_DIGEST_CHUNKED") else True          # a blank line (``KEY=``) is unset here, as ever


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    """``typed_env.env_int`` (unparseable -> default + one warning), then this module's bounds."""
    return max(lo, min(hi, env_int(name, default)))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    """``typed_env.env_float`` (unparseable -> default + one warning), then this module's bounds."""
    return max(lo, min(hi, env_float(name, default)))


def est_tokens(text: str) -> int:
    """A deliberately CONSERVATIVE token estimate (3.3 characters per token; Gemma measures about 4 on English): the prompt must fit the slot, so the guess errs high.
    Identical to ``night_mind.est_tokens``."""
    return int(math.ceil(len(text or "") / 3.3))


def ctx_tokens() -> int:
    return _env_int("ZOE_BRAIN_SLOT_TOKENS", 8192, 2048, 262144)


def max_chunks() -> int:
    return _env_int("ZOE_DIGEST_MAX_CHUNKS", 5, 1, 20)


def max_facts() -> int:
    return _env_int("ZOE_DIGEST_MAX_FACTS", 40, 1, 200)


def decode_tok_s() -> float:
    """The decode rate the timeouts are sized for: ``ZOE_DIGEST_DECODE_TOK_S``, default 60 = the live 4B as measured 2026-10-09 in the night-mind smoke
    (``z0n_smoke.sh``, 17 calls: 60.2 tok/s with the prefill taken off). A config read, never a constant in the call path: the 12B night window runs at ~5."""
    return _env_float("ZOE_DIGEST_DECODE_TOK_S", 60.0, 0.5, 500.0)


def prefill_tok_s() -> float:
    """The prompt-processing rate the timeouts are sized for: ``ZOE_DIGEST_PREFILL_TOK_S``, default 650 = the live 4B (``night_mind.PREFILL_TOK_S``). The 12B night
    window measures its own (~136) and exports it beside ``ZOE_DIGEST_DECODE_TOK_S``."""
    return _env_float(PREFILL_ENV, PREFILL_TOK_S, 5.0, 20000.0)


def default_chunk_tokens(ctx: int) -> int:
    return int(max(600, min(12000, BASE_CHUNK_TOKENS * (ctx / BASE_CTX))))


def chunk_budget(fixed_tokens: int, out_tokens: int, *, ctx: Optional[int] = None) -> int:
    """Turn-tokens one map call may carry: the night mind's 2,400 at 8k (scaled with the slot), never more than what is left of the slot
    once the instructions (``fixed_tokens``), the reply cap (``out_tokens``) and the margin are counted."""
    ctx = ctx or ctx_tokens()
    want = _env_int("ZOE_DIGEST_CHUNK_TOKENS", 0, 0, 20000) or default_chunk_tokens(ctx)
    room = ctx - fixed_tokens - out_tokens - MARGIN_TOKENS
    return max(MIN_BUDGET, min(want, room))


def timeout_for(prompt_tokens: int, max_tokens: int, *, rate: Optional[float] = None, prefill: Optional[float] = None) -> float:
    """The HTTP timeout one call needs: prompt / prefill rate + the reply cap / decode rate + slack, floored (``night_mind.timeout_for``, the same formula)."""
    return round(max(FLOOR_S, prompt_tokens / max(prefill or prefill_tok_s(), 5.0) + max_tokens / max(rate or decode_tok_s(), 0.5) + SLACK_S), 1)


def call_timeout(legacy_s: float, prompt_tokens: int, max_tokens: int) -> float:
    """The timeout of one chunked-mode map call: what the output needs, but never SHORTER than the fixed timeout the pass always had."""
    return max(float(legacy_s), timeout_for(prompt_tokens, max_tokens))


# ═══ pack ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════

_FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|my|we|our|we're|we've)\b", re.IGNORECASE)
_DATEISH_RE = re.compile(r"\b(?:\d{1,2}(?:st|nd|rd|th)?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|"
                         r"july|august|september|october|november|december|tomorrow|tonight|next\s+\w+)\b", re.IGNORECASE)
_NAME_RE = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]{2,}\b")


def signal_score(text: str) -> int:
    """How likely a turn is about the owner's life, in code (only used to choose what a very full day keeps): a name, a date, first person,
    a feeling, anything safety-ish. 0 = a command or small talk. Same weights as ``night_mind.pre_score``."""
    s = 0
    if _NAME_RE.search(text or ""):
        s += 1
    if _DATEISH_RE.search(text or ""):
        s += 1
    if _FIRST_PERSON_RE.search(text or ""):
        s += 1
    try:
        from memory_gate import extract_affect
        if extract_affect(text)[0]:
            s += 2
    except Exception:  # noqa: BLE001
        pass
    try:
        from memory_importance import score_importance
        if score_importance(text) > 0:
            s += 1
    except Exception:  # noqa: BLE001
        pass
    return s


@dataclass
class Chunk:
    """One map call's input: its text and the (0-based) transcript turns it carries."""
    text: str
    turns: "tuple[int, ...]" = ()


@dataclass
class Packed:
    chunks: "list[Chunk]" = field(default_factory=list)
    turns_total: int = 0
    skipped_cap: int = 0            # turns a very full day could not fit in ``max_chunks`` calls


def _pieces(line: str, max_chars: int) -> "list[str]":
    """A line longer than one chunk is cut at whitespace into pieces that each fit (a quote is verified against the whole day, so nothing is lost)."""
    if len(line) <= max_chars:
        return [line]
    out, cur = [], ""
    for word in line.split(" "):
        while len(word) > max_chars:                     # one unbroken string longer than a chunk: hard cut
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:max_chars])
            word = word[max_chars:]
        if cur and len(cur) + 1 + len(word) > max_chars:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    return out


def pack_lines(lines: Sequence[str], budget: int, *, cap: Optional[int] = None,
               score: Callable[[str], int] = signal_score) -> Packed:
    """Consecutive turns (``lines``, in time order) cut into chunks whose text fits ``budget`` tokens each, at most ``cap`` chunks.

    Empty lines are not turns. Over the cap the highest-``score`` turns survive (the newest wins a tie), still in time order, and the rest are
    counted in ``skipped_cap``. Pure."""
    cap = cap or max_chunks()
    items = [(i, ln) for i, ln in enumerate(lines) if (ln or "").strip()]
    packed = Packed(turns_total=len(items))
    max_chars = max(80, int(budget * 3.3) - 4)

    def build(chosen: "list[tuple[int, str]]") -> "list[Chunk]":
        chunks: "list[Chunk]" = []
        cur: "list[str]" = []
        ids: "list[int]" = []
        used = 0
        for i, ln in chosen:
            for piece in _pieces(ln, max_chars):
                cost = est_tokens(piece) + 1
                if cur and used + cost > budget:
                    chunks.append(Chunk("\n".join(cur), tuple(dict.fromkeys(ids))))
                    cur, ids, used = [], [], 0
                cur.append(piece)
                ids.append(i)
                used += cost
        if cur:
            chunks.append(Chunk("\n".join(cur), tuple(dict.fromkeys(ids))))
        return chunks

    chunks = build(items)
    if len(chunks) > cap:
        scored = sorted(range(len(items)), key=lambda k: (-score(items[k][1]), -k))
        room, used, chosen = cap * budget, 0, set()
        for k in scored:
            cost = sum(est_tokens(p) + 1 for p in _pieces(items[k][1], max_chars))
            if used + cost > room:
                continue
            chosen.add(k)
            used += cost
        chunks = build([it for k, it in enumerate(items) if k in chosen])
        # Boundary slack (a turn that does not fit the room left in a chunk starts the next one) can make the chosen turns need more than ``cap`` chunks.
        # Shed the LOWEST-scoring chosen turn (the oldest on a tie) and re-pack until they fit, so the cut falls on the least useful turn and never on
        # whichever turn happens to be the newest.
        for k in sorted(chosen, key=lambda k: (score(items[k][1]), k)):
            if len(chunks) <= cap:
                break
            chosen.discard(k)
            chunks = build([it for j, it in enumerate(items) if j in chosen])
        packed.skipped_cap = len(items) - len({t for c in chunks for t in c.turns})
    packed.chunks = chunks
    return packed


def pack_text(text: str, budget: int, *, cap: Optional[int] = None) -> Packed:
    """``pack_lines`` over a newline-joined transcript. A transcript that already fits one call is ONE chunk holding the text byte for byte (so a
    short day's prompt is exactly what it always was)."""
    lines = (text or "").split("\n")
    nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
    if nonempty and est_tokens(text) <= budget:
        return Packed([Chunk(text, tuple(nonempty))], len(nonempty), 0)
    return pack_lines(lines, budget, cap=cap)


# ═══ coverage ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class PassCoverage:
    """What one pass read: turns in the transcript, turns whose chunk was answered, chunks packed, model calls made, calls that failed."""
    name: str
    chunked: bool = True
    turns_total: int = 0
    turns_read: int = 0
    chunks: int = 0
    calls: int = 0
    failed: int = 0
    skipped_cap: int = 0
    _read: "set[int]" = field(default_factory=set, repr=False)

    def mark_read(self, chunk: Chunk) -> None:
        self._read.update(chunk.turns)
        self.turns_read = len(self._read)


@dataclass
class RunCoverage:
    """One member-night (or one open-loop run): the passes that ran. Its presence also means the run is TOLERANT of a failed chunk (a partial
    night is still a night); a caller without one (the idle consolidation) treats any failed chunk as a failed extraction so its watermark holds."""
    passes: "dict[str, PassCoverage]" = field(default_factory=dict)

    def for_pass(self, name: str) -> PassCoverage:
        return self.passes.setdefault(name, PassCoverage(name))

    def line(self, job: str, user_id: str) -> str:
        ps = list(self.passes.values())
        first = ps[0] if ps else PassCoverage(job)
        extras = " ".join(f"{p.name}_calls={p.calls} {p.name}_turns_read={p.turns_read}" for p in ps)
        return (f"DIGEST_COVERAGE job={job} user={user_id} turns_total={max((p.turns_total for p in ps), default=0)} "
                f"turns_read={first.turns_read} chunks={sum(p.chunks for p in ps)} calls={sum(p.calls for p in ps)} "
                f"failed={sum(p.failed for p in ps)} skipped_cap={sum(p.skipped_cap for p in ps)} chunked={int(first.chunked)} {extras}").strip()


_RUN: "contextvars.ContextVar[Optional[RunCoverage]]" = contextvars.ContextVar("digest_run_coverage", default=None)


def begin_run() -> "tuple[RunCoverage, contextvars.Token]":
    run = RunCoverage()
    return run, _RUN.set(run)


def end_run(token: "contextvars.Token") -> None:
    _RUN.reset(token)


def current() -> Optional[RunCoverage]:
    return _RUN.get()
