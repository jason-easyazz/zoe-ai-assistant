"""Night mind v1 - the nightly reflection pass: the day's own words become a few CITED, POINTER observations.

Why (docs/research/samantha-mind-layer-2026-10-07.md "Reflect"; docs/research/night-mind-2026-10-09.md; the bake-off decision record
2026-10-08 section 6): Zoe filed what the owner said and could answer from it, but nothing turned a month of days into "Tamsin has started the new
job, the knee is better, Rowan's concert went well". Hindsight's reflection cannot run on the live 8,192 slot (its consolidation prompt peaks at
8,313 tokens) and its observations failed the precision veto; MemPalace's closet pass indexes, it does not understand. This takes the SHAPE of both,
not the software: Hindsight's create / update schema with source ids, a mandatory reason, PREFER UPDATE OVER CREATE, ABSENCE IS NOT CONTRADICTION and
a refutation threshold (``consolidation/prompts.py``, ``reflect/prompts.py``); MemPalace's quote discipline - a quote is "EXACT verbatim ... not
paraphrased" and is checked (``closet_llm.py``).

THE MODEL POINTS, CODE DECIDES. Four stages, per household member, inside the nightly digest (``memory_digest.run_memory_digest``):

  1 PACK     (code)  the day's own-words turns (``own_words``-filtered, forgotten turns skipped: the digest's ``Transcript``) lose their routine
                     commands (timers, lights, music, weather), are cut into chunks of ~2,400 tokens of turns (scaled by ``ctx_tokens``), at most
                     ``max_calls - 1`` chunks. This REPLACES the 3,000-character cut the digest's extractors apply (a busy day lost ~70 % of its turns).
  2 MOMENTS  (4B, one call per chunk) the model picks MOMENTS: turn id, a verbatim quote and a few enums (kind / who / feeling / weight / later).
                     It writes no claim. Code checks every moment: the id is in the chunk, the quote is a substring of the cited turn, the enums are
                     in range; a moment that fails is dropped and counted, never repaired. Then ``memory_authority.check_observation`` judges the
                     quote against the turn (a hedged or unsupported quote is HELD, never served) and a quote the owner's later word has replaced is
                     marked history (the authority wall's own conflict test).
  3 THREADS  (4B, one call per member) the model groups the moments into threads: create / update with source moment ids and a reason; a thread
                     not mentioned tonight is left alone (absence is not contradiction); it is resolved only when a moment says it finished.
                     Ids in, ids out - no prose. A returned id that does not exist drops that one operation.
  4 DECIDE   (code)  mood trend, what changed, quiet threads, salience and the raise / leave policy are COMPUTED. ``leave`` is a deny-list
                     (health, grief, money, conflict, "don't bring that up", two ignored raises, too fresh): it is never written into any prompt.
                     At most ONE thread is ``raise`` for the morning. The model's only say is none.

An observation is a POINTER: the ``chat_messages`` id and an exact span of that turn (kept beside it so a forget can erase by text, as
``exact_turns`` does), the day, enums, a validity interval. There is no field for model prose. ``authority_class`` is ``user_stated_derived`` at most.

Call budget per member per night: ``max_calls`` (default 7: up to 6 MOMENTS calls + 1 THREADS call). A quiet day is 0 calls; one chunk with nothing
open is 2. The cap is enforced in code. Prompts are sized with a conservative token estimate so prompt + output stay inside ``ctx_tokens``.

NEVER HALF A NIGHT: the pass computes the whole night in memory and writes at the end. A model that is unreachable, or a transport error on any
call, aborts the member-night with NOTHING written (not even a run row).

READERS (``enforce`` only; each fail-open, bounded, no I/O when the flag is off): ``prompt_block`` (the recall packet's "What I've noticed", <= 3
lines, only when the message names a story or is an open check-in), ``morning_items`` (<= 1 line for the first-turn day brief, the ``raise`` thread,
keyed ``night_threads:<id>`` so the brief's ``mentioned()`` marks it), ``lookup`` (the bench's and the card's ranking).

``ZOE_NIGHT_MIND`` = off (default) | shadow (the calls and every check run, counts only, NOTHING written) | enforce (or 1/true/on).
Standalone: ``scripts/maintenance/zoe-night-mind.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens 16384 --all-members``.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Sequence

import distress_handoff
import lexicons as _lex
import memory_authority as _ma
import night_store

logger = logging.getLogger(__name__)


def change_backed(quote: str) -> bool:
    """Does the owner's own line SAY something changed? A moment the model labels ``kind=change`` only changes a thread's status, retires the
    thread's earlier observations, or skips the stale-belief check when this is true: model points, code decides (K9f: a flat week's "Booked the
    train to X" labelled a change was reported as a changed thread). True when the line carries one of the language's ``change_cues`` or an
    end-state marker (``ended_verbs`` / ``ended_phrases``, not a negated one). A language with no ``change_cues`` keeps the model's label
    (fail-open to the old behaviour, so adding a language never silently mutes its changes). Pure."""
    text = str(quote or "")
    if not text.strip():
        return False
    lang = _lex.detect(text) or "en"
    if not _lex.words(lang, "change_cues"):
        return True
    rx = _lex.word_re(lang, "change_cues")
    if rx is not None and rx.search(_lex._nfkc(text)):
        return True
    return any(kind in ("verb", "phrase") for _a, _b, kind in _lex.ended_spans(text, lang))


def _source_sentence(turn_text: str, quote: str) -> str:
    """The sentence of the owner's turn that holds ``quote`` (the quote widened to its sentence edges; "" when it is not found). Pure."""
    t, q = str(turn_text or ""), str(quote or "").strip()
    i = t.lower().find(q.lower()) if q else -1
    if i < 0:
        return ""
    a = max((t.rfind(c, 0, i) for c in ".!?\n"), default=-1) + 1
    ends = [e for e in (t.find(c, i + len(q)) for c in ".!?\n") if e >= 0]
    return t[a:(min(ends) + 1) if ends else len(t)]


def moment_change_backed(m: "Moment") -> bool:
    """``change_backed`` for a moment: the cited quote, or else the sentence of the owner's turn it sits in (a short quote can leave out the very words that
    say the value changed: "now" / "moved" in the rest of the sentence). A change stated in ANOTHER sentence of the turn does not count. Pure."""
    if change_backed(m.quote):
        return True
    return m.turn is not None and change_backed(_source_sentence(m.turn.text, m.quote))

ENV = "ZOE_NIGHT_MIND"
URL_ENV = "ZOE_NIGHT_MIND_URL"
MODEL_ENV = "ZOE_NIGHT_MIND_MODEL"
MAX_CALLS_ENV = "ZOE_NIGHT_MIND_MAX_CALLS"
CHUNK_ENV = "ZOE_NIGHT_MIND_CHUNK_TOKENS"
CTX_ENV = "ZOE_NIGHT_MIND_CTX_TOKENS"
DECODE_ENV = "ZOE_NIGHT_MIND_DECODE_TOK_S"
PREFILL_ENV = "ZOE_NIGHT_MIND_PREFILL_TOK_S"
SCHEMA_ENV = "ZOE_NIGHT_MIND_SCHEMA"

WRITER = "night_mind"
KINDS = ("progress", "plan", "feeling", "change", "person", "health", "other")
FEELINGS = ("none", "worried", "sad", "angry", "stressed", "happy", "excited", "proud", "relieved", "other")
LATER = ("open", "done", "na")
NEGATIVE = frozenset({"worried", "sad", "angry", "stressed"})
POSITIVE = frozenset({"happy", "excited", "proud", "relieved"})
THREAD_STATUS = ("open", "resolved", "changed", "quiet", "recurring")

MIN_TURNS = 3                       # fewer own-words turns than this (after the routine drop) and the night has nothing to reflect on: zero calls
MIN_WORDS = 20                      # the digest's own quiet-day floor
DEFAULT_MAX_CALLS = 7
BASE_CTX = 8192
BASE_CHUNK_TOKENS = 2400            # the OWNER's words per MOMENTS call at the 8k slot; instructions + output ride on top and stay inside it
MAX_MOMENTS_PER_CHUNK = 8
#: a MOMENTS call READS at most this many lines (1.5 x what it is asked to return). Measured on the 4B (2026-10-10): shown 19 lines and asked for 8 it picked the first eight that sounded
#: personal and skipped a child's concert plan, 3 of 3 seeds; a call that holds about as many lines as it may return has nothing to choose between. The token budget still bounds a call
#: of long lines.
MAX_LINES_PER_CHUNK = 12
MAX_MOMENTS_TO_THREADS = 24
MAX_HELD_ROWS = 6
MAX_OPEN_THREADS_LISTED = 8
TURN_CHARS = 600
QUOTE_MIN_WORDS, QUOTE_MAX_WORDS = 2, 40
MOMENT_MAX_TOKENS = 640          # 8 moments of ~55 tokens each plus the wrapper: the first live smoke measured 450 as too tight (3 of 14 replies were cut off)
MOMENT_TOKENS_EACH = 70          # one compact moment is ~55 tokens, a pretty-printed one ~85 (a model that ignores the one-line instruction): room for the cap's worth plus the wrapper
THREAD_MAX_TOKENS = 450
QUIET_AFTER_DAYS = 9
RESOLVED_SHOW_DAYS = 14
SHOW_LINES = 3
MORNING_DAYS = 3
STALE_CARD_HOURS = 36
DEFAULT_DECODE_TOK_S = 8.0          # the run-2 measurement on this brain; the 12B is slower (the CLI measures and overrides)
PREFILL_TOK_S = 650.0               # the live 4B's prompt rate; the 12B prefills at ~136 tok/s (2026-10-09 window): the window MEASURES it and passes it (--prefill-tok-s / PREFILL_ENV)
PRODUCTION_TEMPERATURE = 0.1         # the member pass's sampling temperature (it was a literal in _complete); the cells measurement pins its own per run (Config.temperature / Config.seed)
TIMEOUT_MARGIN_S = 20.0             # queueing / tokenisation / template slack on top of prefill + decode
TIMEOUT_FLOOR_S = 30.0              # no call is ever given less than this, however fast the rates claim the model is
_CACHE_TTL_S = 90.0

#: the bench seam: names of faults the lab switches ON to prove a cell turns red (``zmb.lab_driver`` controls). Empty in production, always.
FAULTS: "set[str]" = set()


def fault(name: str) -> bool:
    return name in FAULTS


# ═══ config ═══════════════════════════════════════════════════════════════════════════════════════════════════════════════

def mode() -> str:
    """``off`` (default; unset, empty, 0/false/no/off or anything unrecognised) | ``shadow`` | ``enforce`` (1/true/yes/on/enforce). Per-call env read."""
    v = (os.environ.get(ENV) or "").strip().lower()
    if v == "shadow":
        return "shadow"
    if v in ("1", "true", "yes", "on", "enforce"):
        return "enforce"
    return "off"


def enabled() -> bool:
    """Is the layer SERVING (``enforce``)? Shadow writes nothing, so there is nothing to serve."""
    return mode() == "enforce"


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int((os.environ.get(name) or "").strip() or default)))
    except ValueError:
        return default


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float((os.environ.get(name) or "").strip() or default)))
    except ValueError:
        return default


def est_tokens(text: str) -> int:
    """A deliberately CONSERVATIVE token estimate (3.3 characters per token; Gemma's tokenizer measures about 4 on English): the prompt must fit the
    slot, so the guess errs high."""
    return int(math.ceil(len(text or "") / 3.3))


def _normalize_base(raw: str) -> str:
    base = (raw or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base or "http://127.0.0.1:11434"


@dataclass
class Config:
    """Everything a run needs from the environment, resolved once. ``ctx_tokens`` is the slot of the server the pass talks to; the chunk budget
    derives from it (2,400 turn-tokens at 8k, proportionally more at 16k / 32k)."""
    ctx_tokens: int = BASE_CTX
    chunk_tokens: int = BASE_CHUNK_TOKENS
    max_calls: int = DEFAULT_MAX_CALLS
    url: str = ""
    model: str = ""
    decode_tok_s: float = DEFAULT_DECODE_TOK_S
    prefill_tok_s: float = PREFILL_TOK_S
    schema: bool = False
    #: sampling. The member pass runs at ``PRODUCTION_TEMPERATURE`` with no seed (the server picks one). The MEASUREMENT (``zoe-night-mind.py --cells --runs N``) pins both
    #: per run (llama.cpp takes ``temperature`` and ``seed`` per request) so a verdict can be reproduced and a run-to-run difference is the model's, not luck.
    temperature: float = PRODUCTION_TEMPERATURE
    seed: "Optional[int]" = None

    def timeout_for(self, prompt_tokens: int, max_tokens: int) -> float:
        """THE per-call HTTP budget for this run: ``timeout_for`` at this config's two rates. Every model call in the layer (the member pass, the bench's cells,
        the K12 labelling) gets its timeout from here and nowhere else."""
        return timeout_for(prompt_tokens, max_tokens, self.decode_tok_s, self.prefill_tok_s)

    @property
    def chunk_budget(self) -> int:
        """Turn tokens per MOMENTS call, capped so instructions + turns + output + a margin stay inside the slot."""
        room = self.ctx_tokens - fixed_prompt_tokens() - MOMENT_MAX_TOKENS - 300
        return max(120, min(self.chunk_tokens, room))


def default_chunk_tokens(ctx_tokens: int) -> int:
    return int(max(600, min(12000, BASE_CHUNK_TOKENS * (ctx_tokens / BASE_CTX))))


def config_from_env(*, url: str = "", model: str = "", ctx_tokens: Optional[int] = None, max_calls: Optional[int] = None,
                    chunk_tokens: Optional[int] = None, decode_tok_s: Optional[float] = None, prefill_tok_s: Optional[float] = None,
                    temperature: Optional[float] = None, seed: Optional[int] = None) -> Config:
    ctx = int(ctx_tokens or _int_env(CTX_ENV, 0, 0, 262144) or _int_env("ZOE_BRAIN_SLOT_TOKENS", BASE_CTX, 2048, 262144))
    chunk = int(chunk_tokens or _int_env(CHUNK_ENV, 0, 0, 20000) or default_chunk_tokens(ctx))
    return Config(
        ctx_tokens=ctx, chunk_tokens=chunk, max_calls=int(max_calls or _int_env(MAX_CALLS_ENV, DEFAULT_MAX_CALLS, 2, 20)),
        url=_normalize_base(url or os.environ.get(URL_ENV) or os.environ.get("GEMMA_SERVER_URL") or ""),
        model=model or os.environ.get(MODEL_ENV) or os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        decode_tok_s=float(decode_tok_s or _float_env(DECODE_ENV, DEFAULT_DECODE_TOK_S, 0.5, 200.0)),
        prefill_tok_s=max(5.0, float(prefill_tok_s or _float_env(PREFILL_ENV, PREFILL_TOK_S, 5.0, 20000.0))),
        schema=(os.environ.get(SCHEMA_ENV) or "").strip().lower() in ("1", "true", "yes", "on"),
        temperature=PRODUCTION_TEMPERATURE if temperature is None else max(0.0, min(2.0, float(temperature))),
        seed=None if seed is None else int(seed))


#: the lab / CLI seam for the run's Config (the digest hook calls ``run_for_user`` with no config; the bench points it at the clone or sets a small chunk budget)
_CONFIG: "Optional[Config]" = None


def set_config(cfg: "Optional[Config]") -> "Optional[Config]":
    """Install (or clear, with None) the Config the digest hook uses; returns the previous one."""
    global _CONFIG
    prev, _CONFIG = _CONFIG, cfg
    return prev


def timeout_for(prompt_tokens: int, max_tokens: int, decode_tok_s: float, prefill_tok_s: float = PREFILL_TOK_S, *, margin_s: Optional[float] = None) -> float:
    """The HTTP budget for ONE call, the single formula the whole layer uses: ``prompt_tokens / prefill_rate + max_tokens / decode_rate + margin``, never below
    ``TIMEOUT_FLOOR_S``. Both rates are the server's MEASURED ones (the night window probes them and passes ``--decode-tok-s`` / ``--prefill-tok-s``): the 12B prefills
    at ~136 tok/s and decodes at ~3.6, so a 3,000-token prompt with a 640-token cap needs ~220 s, not the ~100 s the 4B's constants give."""
    return round(max(TIMEOUT_FLOOR_S, prompt_tokens / max(prefill_tok_s, 5.0) + max_tokens / max(decode_tok_s, 0.5) + (TIMEOUT_MARGIN_S if margin_s is None else margin_s)), 1)


def observe_decode_rate(cfg: "Config", prompt_tokens: int, completion_tokens: int, seconds: float) -> None:
    """A model slower than the configured decode rate gets LONGER timeouts from then on, never shorter: after a call that generated >= 100 tokens the configured rate
    drops to 90% of what that call measured if that is lower (prefill taken off at ``cfg.prefill_tok_s``). A SAFETY NET only: the first calls of a run use the rates
    they were given (an answer that never arrives is never observed), so the window must pass the measured rates. Calls within one Config run one after the other and the
    update is a monotone ``min``, so it cannot race. Pure but for ``cfg``."""
    if completion_tokens < 100 or seconds <= 0:
        return
    gen_s = max(seconds - prompt_tokens / max(cfg.prefill_tok_s, 5.0), 0.1)
    cfg.decode_tok_s = max(0.5, min(cfg.decode_tok_s, 0.9 * completion_tokens / gen_s))


class ModelUnreachable(RuntimeError):
    """A transport failure: the server did not answer. The member-night is abandoned with nothing written. ``counts`` carries the counters of the calls made
    so far (attached by the call wrapper), so an abandoned night still reports what it spent."""

    def __init__(self, *a: Any):
        super().__init__(*a)
        self.counts: "dict[str, int]" = {}


class ModelTimeout(ModelUnreachable):
    """The server accepted the call and did not answer within the computed budget. Names the budget and the rates it came from, so a slow model reads as a slow
    model (an instrument setting) and not as a dead one."""

    def __init__(self, budget_s: float, prompt_tokens: int, max_tokens: int, cfg: "Config"):
        self.budget_s, self.prompt_tokens, self.max_tokens = budget_s, prompt_tokens, max_tokens
        self.decode_tok_s, self.prefill_tok_s = cfg.decode_tok_s, cfg.prefill_tok_s
        super().__init__(f"ReadTimeout budget_s={budget_s} (prompt~{prompt_tokens} tok @ {cfg.prefill_tok_s:g} tok/s + {max_tokens} tok @ {cfg.decode_tok_s:g} tok/s + {TIMEOUT_MARGIN_S:g} s)")


#: the lab / test seam: ``async|sync (messages, max_tokens) -> str``. The bench's fake brain (``zmb.night_brain``) and the unit tests set it.
_LLM: "Optional[Callable[[list, int], Any]]" = None


def set_llm(fn: "Optional[Callable[[list, int], Any]]") -> "Optional[Callable[[list, int], Any]]":
    """Install (or clear, with None) the model seam; returns the previous one."""
    global _LLM
    prev, _LLM = _LLM, fn
    return prev


#: the lab's TRACE seam: ``fn(record: dict)``. Set only by ``zoe-night-mind.py --cells --trace FILE`` (a scratch household of invented names, never a member): every model call's
#: prompt and reply, and each pass's final threads + changes, so a failed cell can be read instead of guessed at. None (the default) = nothing is recorded.
_TRACE: "Optional[Callable[[dict], None]]" = None


def set_trace(fn: "Optional[Callable[[dict], None]]") -> "Optional[Callable[[dict], None]]":
    """Install (or clear, with None) the trace seam; returns the previous one."""
    global _TRACE
    prev, _TRACE = _TRACE, fn
    return prev


def _trace(record: "dict[str, Any]") -> None:
    if _TRACE is None:
        return
    try:
        _TRACE(record)
    except Exception:  # noqa: BLE001 - a broken trace sink never changes a night
        logger.warning("night_mind: trace sink failed", exc_info=True)


async def probe_model(cfg: Config) -> "tuple[bool, str]":
    """Is the server up? ``(ok, why)``. Nothing is generated. The lab seam is always up."""
    if _LLM is not None:
        return True, "seam"
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
            for path in ("/health", "/v1/models"):
                try:
                    r = await client.get(cfg.url + path)
                except httpx.HTTPError:
                    continue
                if r.status_code < 500:
                    return True, path
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__
    return False, "unreachable"


_MOMENT_SCHEMA = {
    "type": "object", "required": ["moments"], "additionalProperties": False,
    "properties": {"moments": {"type": "array", "maxItems": MAX_MOMENTS_PER_CHUNK, "items": {
        "type": "object", "required": ["ids", "quote", "kind", "who", "feeling", "weight", "later"], "additionalProperties": False,
        "properties": {"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3}, "quote": {"type": "string"},
                       "kind": {"enum": list(KINDS)}, "who": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                       "feeling": {"enum": list(FEELINGS)}, "weight": {"type": "integer", "minimum": 1, "maximum": 3}, "later": {"enum": list(LATER)}}}}},
}


async def _complete(messages: list, max_tokens: int, cfg: Config, usage: "dict[str, int]", *, schema: "Optional[dict]" = None) -> str:
    if _LLM is not None:
        out = _LLM(messages, max_tokens)
        if asyncio.iscoroutine(out) or isinstance(out, Awaitable):
            out = await out
        text = str(out or "")
        usage["prompt_tokens"] += sum(est_tokens(m["content"]) for m in messages)
        usage["completion_tokens"] += est_tokens(text)
        return text
    import httpx

    payload: "dict[str, Any]" = {"model": cfg.model, "messages": messages, "max_tokens": max_tokens, "temperature": cfg.temperature, "stream": False}
    if cfg.seed is not None:
        payload["seed"] = int(cfg.seed)
    if schema is not None and cfg.schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "night_mind", "strict": True, "schema": schema}}
    prompt_est = sum(est_tokens(m["content"]) for m in messages)
    timeout = cfg.timeout_for(prompt_est, max_tokens)
    t_call = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as client:
            resp = await client.post(f"{cfg.url}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        body = resp.json()
    except httpx.ReadTimeout as exc:
        raise ModelTimeout(timeout, prompt_est, max_tokens, cfg) from exc
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError) as exc:
        raise ModelUnreachable(type(exc).__name__) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            raise ModelUnreachable(f"HTTP {exc.response.status_code}") from exc
        raise
    u = body.get("usage") or {}
    usage["prompt_tokens"] += int(u.get("prompt_tokens") or prompt_est)
    text = str(body["choices"][0]["message"]["content"] or "")
    usage["completion_tokens"] += int(u.get("completion_tokens") or est_tokens(text))
    observe_decode_rate(cfg, int(u.get("prompt_tokens") or prompt_est), int(u.get("completion_tokens") or est_tokens(text)), time.monotonic() - t_call)
    return text


# ═══ prompts (sha-pinned in every run row) ════════════════════════════════════════════════════════════════════════════════

MOMENTS_SYSTEM = "You read one person's own words. You point at moments; you never explain them. Return ONLY valid JSON."

MOMENTS_USER = """TASK: MOMENTS
You read one person's own words from today. Each line is [id] day: words.

{lines}

Every line above is something this person chose to say. Make a moment of EVERY line except a command (lights, timers, music, weather, sums) or small talk: what they are doing, learning or practising, a plan or date, a feeling or worry, a change, something about someone they know, a health matter. At most {cap}; if more lines qualify, keep the {cap} that matter most to knowing this person: cover every different person or matter once (its newest line) before taking a second line about the same one. List them in line order, one moment per line. Do not explain, conclude or guess a reason.
Return JSON, one object per moment (two shown): {{"moments":[{{"ids":["m3"],"quote":"...","kind":"progress","who":["Dagny"],"feeling":"none","weight":2,"later":"open"}},{{"ids":["m5"],"quote":"...","kind":"feeling","who":[],"feeling":"worried","weight":2,"later":"na"}}]}}, at most {cap}.
ids: the line ids it comes from (1-3). quote: copied EXACTLY, letter for letter, from ONE of those lines (3-25 words).
kind = the main thing the line is about: health (a body, an illness, a doctor) | feeling (how they feel: worry, pride, joy) | plan (dated, or still to happen) | change (something moved, ended, was called off or replaced) | progress (something going on or achieved: a project, a habit, a practice, a job, an offer, a result) | person (only a plain fact about someone they know) | other. who: names the person mentions in it ([] if only themselves).
feeling: none|worried|sad|angry|stressed|happy|excited|proud|relieved|other. weight: 1 a passing remark or a plain fact, 2 something they care about or keep coming back to, 3 big news, a major event or a serious worry.
later: open (still to happen or unresolved) | done | na.
If every line is a command or small talk, return {{"moments":[]}}.
Write the JSON on ONE line: no code fence, no indentation, no line breaks (the reply has a hard length limit and a cut-off reply loses the newest lines)."""

THREADS_SYSTEM = "You tidy one person's night notes. You only choose and group by id; you never write new sentences. Return ONLY valid JSON."

THREADS_USER = """TASK: THREADS
OPEN THREADS (from earlier nights):
{old}

TONIGHT'S MOMENTS (id [day] kind feeling: "quote"):
{new}

Group tonight's moments into threads. One thread per person-or-matter. Prefer to UPDATE an open thread over creating a new one; a create must say in its reason which open thread you considered and why none matched. A thread that is not mentioned tonight is left alone (put it in "unchanged"): not being mentioned is not the same as being over. A thread is "resolved" only when a moment says it finished. Copy ids exactly. Never write a cause ("because") unless a quote says it.
Return JSON: {{"threads":[{{"op":"create","title":"<=8 words","moments":["q3","q7"],"status":"open","reason":"..."}},{{"op":"update","thread":"t2","moments":["q5"],"status":"open","reason":"..."}}],"unchanged":["t4"]}}
status: open|resolved|changed. If there are no moments, return {{"threads":[],"unchanged":[]}}."""


def moment_max_tokens(cap: int = MAX_MOMENTS_PER_CHUNK) -> int:
    """The output limit of ONE MOMENTS call that may return ``cap`` moments: never below ``MOMENT_MAX_TOKENS`` (the 8-moment limit), more when a caller asks for more (the K12 labelling)."""
    return max(MOMENT_MAX_TOKENS, 80 + MOMENT_TOKENS_EACH * max(1, int(cap)))


def fixed_prompt_tokens() -> int:
    """The instruction part of the MOMENTS prompt (what rides on top of the owner's words)."""
    return est_tokens(MOMENTS_SYSTEM) + est_tokens(MOMENTS_USER.format(lines="", cap=MAX_MOMENTS_PER_CHUNK))


def prompt_sha() -> str:
    return hashlib.sha1("\n".join((MOMENTS_SYSTEM, MOMENTS_USER, THREADS_SYSTEM, THREADS_USER)).encode()).hexdigest()[:12]


def schema_sha() -> str:
    return hashlib.sha1(json.dumps(_MOMENT_SCHEMA, sort_keys=True).encode()).hexdigest()[:12]


# ═══ data ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class Turn:
    id: str
    text: str
    at: "_dt.datetime"


@dataclass
class Moment:
    mid: str = ""
    turn: "Optional[Turn]" = None
    quote: str = ""
    kind: str = "other"
    who: "list[str]" = field(default_factory=list)
    feeling: str = "none"
    weight: int = 1
    later: str = "na"
    chunk: int = 0
    state: str = "current"          # current | held | history
    basis: str = ""
    why_held: str = ""
    thread: str = ""                # thread id once placed

    @property
    def day(self) -> str:
        return local_day(self.turn.at) if self.turn else ""

    @property
    def valence(self) -> int:
        return -1 if self.feeling in NEGATIVE else (1 if self.feeling in POSITIVE else 0)


def _local_tz():
    try:
        from time_utils import zoe_timezone

        return zoe_timezone()
    except Exception:  # noqa: BLE001 - slim lanes without the zone data: UTC dates
        return _dt.timezone.utc


def parse_ts(raw: Any) -> "Optional[_dt.datetime]":
    """A stored timestamp (ISO text, a Postgres ``2026-10-08 14:03:22+00``, a datetime or epoch seconds) as an aware datetime, else None."""
    if raw in (None, ""):
        return None
    if isinstance(raw, _dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=_dt.timezone.utc)
    if isinstance(raw, (int, float)):
        return _dt.datetime.fromtimestamp(float(raw), _dt.timezone.utc)
    s = str(raw).strip().replace("Z", "+00:00")
    if re.fullmatch(r".*[+-]\d\d$", s):
        s += ":00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def local_day(at: "_dt.datetime") -> str:
    return at.astimezone(_local_tz()).date().isoformat()


def fmt_day(iso: str) -> str:
    try:
        d = _dt.date.fromisoformat(iso[:10])
        return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    except ValueError:
        return iso


def turns_of(transcript: Any, now: "_dt.datetime") -> "list[Turn]":
    """The digest ``Transcript``'s ``(message_id, text)`` turns with their times; a bare string (no ids) has nothing to cite and gives none."""
    pairs = list(getattr(transcript, "turns", ()) or ())
    times = list(getattr(transcript, "times", ()) or ())
    out: "list[Turn]" = []
    for i, (mid, text) in enumerate(pairs):
        mid, text = str(mid or "").strip(), re.sub(r"\s+", " ", str(text or "")).strip()
        if mid and text and not distress_handoff.guarded_text(text):   # a distress hand-off turn is never reflected on, raised or quoted (docs/knowledge/distress-handoff.md)
            out.append(Turn(mid, text, (parse_ts(times[i]) if i < len(times) else None) or now))
    return out


# ═══ stage 1: pack ════════════════════════════════════════════════════════════════════════════════════════════════════════

_STOP = frozenset("""a an and are as at be been but by for from had has have he her his how i if in is it its me my of on or our she so than that the their
them then there they this to up was we were what when where which who why will with you your about after again all also any because before being between both can
could did do does doing down during each few further get got here into just like more most no nor not now off once only other out over own really same should some
still such too under until very week lately going tell told say said anything something someone""".split())
_ROUTINE_RE = re.compile(
    r"^\s*(?:hey\s+|ok(?:ay)?\s+|please\s+)?(?:zoe[,\s]+)?(?:turn|switch|set|play|pause|resume|stop|skip|next|previous|volume|mute|unmute|dim|brighten|"
    r"open|close|start|cancel|add|put|remind|read|show|call|text|send|good\s+(?:morning|night)|thanks?|thank\s+you|calculate|convert|"
    r"what(?:'s|\s+is)\s+the\s+(?:time|weather|date)|what\s+time|how\s+(?:hot|cold|many|much)|how\s+is\s+the)\b|"
    r"\b(?:weather|timer|alarm|lights?|volume|playlist|thermostat|temperature)\b", re.IGNORECASE)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|i've|i'd|i'll|my|we|our|we're|we've)\b", re.IGNORECASE)
_DATEISH_RE = re.compile(r"\b(?:\d{1,2}(?:st|nd|rd|th)?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|"
                         r"july|august|september|october|november|december|tomorrow|tonight|next\s+\w+)\b", re.IGNORECASE)
_CAP_RE = re.compile(r"\b[A-Z][a-z]{2,}\b(?:['’]s)?")
_OPENERS = frozenset("""remind turn set play pause skip stop good thanks thank please what where when how can could would will don't dont let tell read show call text
send add put open close start cancel switch dim brighten mute unmute hey okay yes yeah sure maybe also but and then today tomorrow tonight yesterday monday tuesday wednesday
thursday friday saturday sunday january february march april may june july august september october november december""".split())


def names_in(text: str) -> "list[str]":
    """The named people / places / organisations in a turn: capitalised words, not the first word of a sentence unless it is not an ordinary opener or function word
    ('Dagny's offer ...' names Dagny; 'Remind me ...' names nobody). Lower-cased, possessive stripped, in order, de-duplicated. Pure."""
    out: "list[str]" = []
    for m in _CAP_RE.finditer(text or ""):
        w = re.sub(r"['’]s$", "", m.group(0)).lower()
        start = m.start()
        initial = start == 0 or bool(re.search(r"[.!?]\s+$", text[:start]))
        if initial and (w in _STOP or w in _OPENERS):
            continue
        if w in _NOT_NAMES or w in out:
            continue
        out.append(w)
    return out


def _words(text: str) -> "list[str]":
    t = re.sub(r"['’]s\b", "", (text or "").lower())
    return [w for w in re.findall(r"[a-z0-9]{3,}", t) if w not in _STOP]


def pre_score(text: str) -> int:
    """How likely a turn is about the owner's life, in code: names, dates, first person, a feeling, anything safety-ish. 0 = a command or small talk."""
    s = 0
    if names_in(text):
        s += 1
    if _DATEISH_RE.search(text):
        s += 1
    if _FIRST_PERSON_RE.search(text):
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


def is_routine(text: str) -> bool:
    """A routine command or small talk, dropped before the model sees it: a command-shaped turn that carries little of the owner's life, or a turn
    too short to say anything. Deterministic (the share of a real day this removes is experiment E1)."""
    sc = pre_score(text)
    if _ROUTINE_RE.search(text) and sc < 2:
        return True
    return len(_words(text)) < 3 and sc == 0


def _line(alias: str, t: Turn) -> str:
    return f"[{alias}] {fmt_day(local_day(t.at))}: {t.text[:TURN_CHARS]}"


def chunk_turns(turns: "Sequence[Turn]", budget: int, max_lines: int = 0) -> "list[list[Turn]]":
    """Consecutive turns cut into chunks whose lines fit ``budget`` tokens each (a single over-long turn is truncated to ``TURN_CHARS``, so it fits)."""
    chunks: "list[list[Turn]]" = []
    cur: "list[Turn]" = []
    used = 0
    for t in turns:
        cost = est_tokens(_line("m999", t)) + 1
        if cur and (used + cost > budget or len(cur) >= (max_lines or MAX_LINES_PER_CHUNK)):
            chunks.append(cur)
            cur, used = [], 0
        cur.append(t)
        used += cost
    if cur:
        chunks.append(cur)
    return chunks


def pack(turns: "Sequence[Turn]", cfg: Config) -> "tuple[list[list[Turn]], dict[str, int]]":
    """Stage 1: drop routine turns, chunk by token budget, cap the chunks (the highest pre-scored turns survive a very full day, in time order).
    Returns ``(chunks, counts)``. Under the ``chunking`` fault (the bench's control) this is the OLD digest: no drop, no chunks, the transcript cut at
    3,000 characters."""
    stats = {"turns_in": len(turns), "turns_dropped_routine": 0, "turns_skipped_cap": 0}
    if fault("chunking"):
        keep, used = [], 0
        for t in turns:
            used += len(t.text) + 1
            if used > 3000:
                stats["turns_skipped_cap"] += 1
                continue
            keep.append(t)
        return ([keep] if keep else []), stats
    kept = [t for t in turns if not is_routine(t.text)]
    stats["turns_dropped_routine"] = len(turns) - len(kept)
    room = max(1, cfg.max_calls - 1)
    chunks = chunk_turns(kept, cfg.chunk_budget)
    if len(chunks) > room:
        scored = sorted(range(len(kept)), key=lambda i: (-pre_score(kept[i].text), -i))
        cap_tokens = room * cfg.chunk_budget
        chosen, used = set(), 0
        for i in scored:
            cost = est_tokens(_line("m999", kept[i])) + 1
            if used + cost > cap_tokens or len(chosen) >= room * MAX_LINES_PER_CHUNK:
                continue
            chosen.add(i)
            used += cost
        stats["turns_skipped_cap"] = len(kept) - len(chosen)
        kept = [t for i, t in enumerate(kept) if i in chosen]
        chunks = chunk_turns(kept, cfg.chunk_budget)[:room]
    return chunks, stats


# ═══ stage 2: moments ═════════════════════════════════════════════════════════════════════════════════════════════════════

def _sq(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _json_object(raw: str) -> "Optional[dict]":
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip())
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        got = json.loads(s[a:b + 1])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def _salvage(raw: str, key: str) -> "Optional[dict]":
    """A reply cut off by ``max_tokens`` (measured on the live 4B: 3 of 14 calls hit the 450-token cap and the JSON ended mid-object): the COMPLETE objects of ``key``'s
    list that arrived, as ``{key: [...]}``; ``None`` when there are none. Every one still goes through the same checks, so a salvaged moment is no more trusted than a whole one."""
    s = re.sub(r"^```(?:json)?\s*", "", str(raw or "").strip())
    i = s.find(f'"{key}"')
    j = s.find("[", i) if i >= 0 else -1
    if j < 0:
        return None
    dec, pos, items = json.JSONDecoder(), j + 1, []
    while pos < len(s):
        while pos < len(s) and s[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(s) or s[pos] != "{":
            break
        try:
            obj, pos = dec.raw_decode(s, pos)
        except ValueError:
            break
        if isinstance(obj, dict):
            items.append(obj)
    return {key: items} if items else None


def _reply_object(raw: str, key: str, counts: "dict[str, int]") -> "Optional[dict]":
    """The reply's JSON object, or the salvaged complete items of ``key`` (counted), or ``None``."""
    got = _json_object(raw)
    if got is not None and isinstance(got.get(key, []), list):
        return got
    saved = _salvage(raw, key)
    if saved is not None:
        counts["calls_salvaged"] += 1
    return saved


def locate_quote(quote: str, turn_text: str) -> Optional[str]:
    """The owner's exact span: ``quote`` (whitespace- and case-normalised) found inside the turn, returned with the TURN's own letters; else None."""
    q, t = _sq(quote), _sq(turn_text)
    if not q:
        return None
    i = t.lower().find(q.lower())
    return t[i:i + len(q)] if i >= 0 else None


def verify_moment(item: Any, chunk: "Sequence[Turn]", alias: "dict[str, Turn]", chunk_no: int, counts: "dict[str, int]") -> Optional[Moment]:
    """One MOMENTS entry checked in code. A moment that fails (cited id not in the chunk, quote not an exact span of ONE cited turn, wrong length) is
    DROPPED and counted, never repaired. Enums out of range fall to their neutral value (they point, they do not claim)."""
    counts["moments_proposed"] += 1
    if not isinstance(item, dict):
        counts["moments_dropped_id"] += 1
        return None
    ids_raw = item.get("ids")
    ids_raw = ids_raw if isinstance(ids_raw, list) else ([ids_raw] if ids_raw else [])
    cited = [alias[str(x).strip().strip("[]")] for x in ids_raw if str(x).strip().strip("[]") in alias]
    if not cited:
        counts["moments_dropped_id"] += 1
        return None
    quote = _sq(item.get("quote"))
    n = len(quote.split())
    if not (QUOTE_MIN_WORDS <= n <= QUOTE_MAX_WORDS):
        counts["moments_dropped_quote"] += 1
        return None
    turn, span = None, None
    for t in cited:
        span = locate_quote(quote, t.text)
        if span:
            turn = t
            break
    if span is None and fault("citations"):
        turn, span = cited[0], quote                       # the bench's control: the quote and the id are believed as written
    if span is None or turn is None:
        counts["moments_dropped_quote"] += 1
        return None
    if _unstorable(span):                                        # a card number / PIN, or an instruction aimed at Zoe: never kept, never served (as exact_words)
        counts["moments_dropped_quote"] += 1
        return None
    kind = str(item.get("kind") or "other").lower()
    feeling = str(item.get("feeling") or "none").lower()
    later = str(item.get("later") or "na").lower()
    try:
        weight = max(1, min(3, int(item.get("weight") or 1)))
    except (TypeError, ValueError):
        weight = 1
    if fault("weights"):                                         # the bench's random labeller: deterministic scramble of the three enums
        h = int(hashlib.sha1(span.encode()).hexdigest(), 16)
        kind, feeling, weight = KINDS[h % len(KINDS)], FEELINGS[(h >> 8) % len(FEELINGS)], 1 + (h >> 16) % 3
    who = [re.sub(r"\s+", " ", str(w)).strip()[:40] for w in (item.get("who") or []) if isinstance(item.get("who"), list) and str(w).strip()][:4]
    return Moment(turn=turn, quote=span, kind=kind if kind in KINDS else "other", who=who, feeling=feeling if feeling in FEELINGS else "none",
                  weight=weight, later=later if later in LATER else "na", chunk=chunk_no)


def _unstorable(span: str) -> bool:
    try:
        from memory_service import scrub_pii

        redacted, why = scrub_pii(span)
        if why or redacted != span:        # a labelled password / passcode / API key comes back REDACTED with no reason: it is still a secret - never kept
            return True
    except Exception:  # noqa: BLE001 - the PII scrubber is the memory service's: if it cannot be loaded the quote is not kept (fail closed)
        return True
    try:
        import own_words

        return bool(own_words.instruction_shaped(span))
    except Exception:  # noqa: BLE001
        return False


def parse_moments(raw: str, chunk: "Sequence[Turn]", chunk_no: int, counts: "dict[str, int]", cap: int = MAX_MOMENTS_PER_CHUNK) -> "Optional[list[Moment]]":
    """The verified moments of one reply (at most ``cap``); ``None`` = the reply was not usable JSON (counted as invalid), ``[]`` = a valid empty answer."""
    got = _reply_object(raw, "moments", counts)
    if got is None or not isinstance(got.get("moments"), list):
        return None
    alias = {f"m{i}": t for i, t in enumerate(chunk, 1)}
    out: "list[Moment]" = []
    for item in got["moments"][:cap * 2]:
        m = verify_moment(item, chunk, alias, chunk_no, counts)
        if m is not None:
            out.append(m)
    # ``cap`` is what the prompt ASKS for. A model that writes more (the 4B wrote 12 for "at most 8", 2026-10-10) has already paid for them, and every one is verified above: cutting the
    # reply at 8 threw away the LATER lines it had picked (a child's cello lessons) and left them to a second ask that did not pick them again. Up to twice the ask is kept;
    # ``MAX_MOMENTS_TO_THREADS`` still bounds what the THREADS call sees.
    return out


# ═══ judging a verified quote ═════════════════════════════════════════════════════════════════════════════════════════════

def unreached_tail(chunk: "Sequence[Turn]", got: "Sequence[Moment]") -> "list[Turn]":
    """The lines of ``chunk`` after the last one a reply that stopped short (cut off, or at its cap) reached: the model lists moments in line order, so what it never got to is the
    tail, and the tail is the NEWEST part of the day (a change, a finish). ``[]`` when the reply cited nothing (no way to tell where it stopped) or reached the last line."""
    pos = {id(t): i for i, t in enumerate(chunk)}
    last = max((pos[id(m.turn)] for m in got if m.turn is not None and id(m.turn) in pos), default=-1)
    return list(chunk[last + 1:]) if last >= 0 else []


def judge_moment(m: Moment) -> None:
    """``memory_authority.check_observation`` over the quote and the turn it points at: supported -> ``current`` (class ``user_stated_derived``); a
    hedged or unsupported quote is HELD (a pending candidate, never served). With the observation gate off (``ZOE_DIGEST_OBSERVATION_GATE=off``, the
    bench's control) every quote is believed."""
    if m.turn is None:
        m.state, m.why_held = "held", "uncited"
        return
    if _ma.observation_gate_mode() == "off" or fault("citations"):
        m.state, m.basis = "current", "ungated"
        return
    v = _ma.check_observation(m.quote, m.turn.text)
    if v.kind == "supported":
        m.state, m.basis = "current", v.basis or "anchored_user_turn"
    else:
        if _ma.observation_gate_mode() == "shadow":
            m.state, m.basis = "current", "shadow"
            return
        m.state, m.why_held = "held", v.kind + (":" + ",".join(v.reasons) if v.reasons else "")


_RELATION_LEAD = re.compile(r"^\s*(?:my|our)\s+[a-z]+(?:-[a-z]+)?\s+(?=[A-Z])")


def _stale_forms(quote: str) -> "list[str]":
    """The quote as the owner said it, and without a leading 'my sister ' (the authority wall's subject matcher compares 'Jarvis lives in Oldmere' with
    'Jarvis lives in Pellham', not with 'My sister Jarvis ...')."""
    bare = _RELATION_LEAD.sub("", quote or "")
    return [quote] + ([bare] if bare != quote else [])


async def mark_replaced(svc: Any, user_id: str, moments: "Sequence[Moment]") -> int:
    """A current quote the owner's later word has REPLACED is history, not a belief: the authority wall's own conflict test (an approved row the owner
    said, outranking a derived reading, that the quote contradicts). One read of the approved rows per night. Fail-open."""
    if svc is None or not _ma.active() or not moments:
        return 0
    try:
        rows = await svc.list_by_status(user_id=user_id, status="approved", limit=10_000)
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for m in moments:
        if m.state != "current" or (m.kind == "change" and moment_change_backed(m)):
            continue
        hit = None
        for form in _stale_forms(m.quote):
            try:
                hit = _ma.find_conflict(form, rows, _ma.DERIVED_RANK)
            except Exception:  # noqa: BLE001
                hit = None
            if hit is not None:
                break
        if hit is not None:
            m.state, m.why_held = "history", "replaced"
            n += 1
    return n


# ═══ stage 3: threads ═════════════════════════════════════════════════════════════════════════════════════════════════════

_TOPIC_WORDS: "dict[str, tuple[str, ...]]" = {
    # whole words; a trailing * makes it a prefix ("diagnos*" = diagnosed, diagnosis)
    "health": ("knee", "doctor", "dr", "physio*", "hospital", "pain", "sore", "sick", "medication", "diagnos*", "surgery", "operation", "cancer", "therapy",
               "injur*", "scan", "symptom*", "headache", "migraine", "blood pressure", "infection", "rash", "allerg*", "test results", "biopsy"),
    "grief": ("died", "death", "funeral", "passed away", "grief", "grieving", "bereave*", "miss him", "miss her", "lost my"),
    "money": ("debt", "loan", "loans", "owe", "afford", "broke", "bankrupt*", "overdraft", "behind on rent", "can't pay", "cannot pay", "arrears"),
    "conflict": ("argued", "argument", "fight", "fought", "divorce*", "separat*", "split up", "yelled", "fell out", "not speaking", "screamed"),
    "owner_said_no": ("don't bring", "dont bring", "don't mention", "do not mention", "between us", "keep that private", "forget i said", "forget that i",
                      "don't tell", "never bring"),
}
_TOPIC_RX: "dict[str, re.Pattern]" = {
    topic: re.compile("|".join((r"(?<![a-z])" + re.escape(w[:-1]) + r"[a-z]*") if w.endswith("*") else (r"(?<![a-z])" + re.escape(w) + r"(?![a-z])") for w in ws), re.IGNORECASE)
    for topic, ws in _TOPIC_WORDS.items()}
#: single words that name a matter by themselves (a knee is a knee story whoever else is in the sentence): one shared word of these joins a thread
_TOPIC_VOCAB = frozenset(w for ws in _TOPIC_WORDS.values() for w in ws if " " not in w and "*" not in w and len(w) >= 4) - {"results", "dentist", "loan", "loans", "scan", "pain", "sick"}


def topic_of(texts: Iterable[str]) -> str:
    low = " ".join(texts)
    for topic in ("owner_said_no", "grief", "money", "conflict", "health"):
        if _TOPIC_RX[topic].search(low):
            return topic
    return ""


def anchors_of(moments: "Sequence[Moment]", title: str = "") -> "set[str]":
    """The words a thread is recognised by: ``@name`` for each NAMED person / place in its quotes, and its distinctive words."""
    out: "set[str]" = set()
    for m in moments:
        out |= {"@" + n for n in _names_of(m)}
        out |= {w for w in _words(m.quote)[:14]}
    out |= set(_words(title))
    return out


_ANCHOR_STOP = frozenset({"offer", "house", "week", "today", "morning", "night", "feel", "feeling", "think", "want", "need", "going", "make", "take",
                          "have", "much", "really", "after", "soon", "next", "there", "with"})


#: the model writes the OWNER into ``who`` as "self" / "me" / "user" (measured, K10): that is nobody's name, and as an anchor it glued a sore knee and a loan into one thread
_SELF_WORDS = frozenset({"self", "myself", "me", "user", "owner", "you", "them", "themselves", "someone", "somebody", "everyone", "nobody", "family", "friend", "friends"})
_NOT_NAMES = _SELF_WORDS | frozenset({"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may", "june", "july",
                        "august", "september", "october", "november", "december", "zoe", "dr", "mum", "dad"})


def _names_of(m: Moment) -> "set[str]":
    out = {w.lower() for name in m.who for w in re.findall(r"[A-Za-z]{3,}", name)} | set(names_in(m.quote))
    return out - _NOT_NAMES


def thread_match(group: "Sequence[Moment]", thread: dict) -> bool:
    """Does a group of moments continue this stored thread? By code, whatever the model said. A group that names a person (or a place) continues a thread
    only through that name; a group that names nobody ('my knee is better') continues one through two shared distinctive words."""
    anchors = set(str(thread.get("anchors") or "").split())
    if not anchors or not group:
        return False
    names = set().union(*[_names_of(m) for m in group])
    t_names = {a[1:] for a in anchors if a.startswith("@")}
    if names & t_names:
        return True
    if names and t_names:                    # both name somebody, and not the same somebody: two different stories
        return False
    words = set().union(*[set(_words(m.quote)) for m in group]) - _ANCHOR_STOP
    shared = words & {a for a in anchors if not a.startswith("@")} - _ANCHOR_STOP
    return len(shared) >= 2 or bool(shared & _TOPIC_VOCAB)


def thread_identity(title: str, quotes: "Sequence[str]") -> dict:
    """What identifies a thread in raw text, by code and with no language cue: its title's named people / places, any name its quotes repeat (in two or more), and
    the distinctive words its title and quotes share. ``{"anchors": "@name word ..."}`` for ``thread_match``. Pure."""
    names = set(names_in(title or ""))
    seen: "dict[str, int]" = {}
    for q in quotes:
        for n in set(names_in(q)):
            seen[n] = seen.get(n, 0) + 1
    names |= {n for n, c in seen.items() if c >= 2}
    words = set(_words(title or "")) & set().union(*[set(_words(q)) for q in quotes]) if quotes else set()
    return {"anchors": " ".join(sorted({"@" + n for n in names - _NOT_NAMES} | (words - _ANCHOR_STOP)))}


def spoken_since(title: str, quotes: "Sequence[str]", raw_turns: "Sequence[Turn]", last_day: str, today: "_dt.date") -> bool:
    """Did the owner talk about this thread in a raw turn AFTER the thread's last kept mention? Quiet is a fact about the owner's words, not about the moments the model
    happened to keep: a model that drops the latest of a thread's mentions must not make a thread that is still being talked about look abandoned (K9f). The same
    name / shared-word test that continues a thread (``thread_match``) is applied to each later turn. Pure."""
    last = _date(last_day)
    ident = thread_identity(title, quotes)
    if last is None or not ident["anchors"]:
        return False
    for tu in raw_turns:
        d = _date(local_day(tu.at))
        if d is not None and last < d <= today and thread_match([Moment(turn=tu, quote=tu.text)], ident):
            return True
    return False


def _render_new(moments: "Sequence[Moment]") -> str:
    return "\n".join(f"{m.mid} [{fmt_day(m.day)}] {m.kind} {m.feeling}: \"{m.quote[:140]}\"" for m in moments)


def _render_old(threads: "Sequence[dict]", newest: "dict[str, str]") -> str:
    return "\n".join(f"{t['id']} \"{t['title']}\" last {fmt_day(t['last_day'])}: \"{newest.get(t['id'], '')[:100]}\"" for t in threads) or "(none)"


@dataclass
class Group:
    moments: "list[Moment]"
    thread_id: str = ""          # an existing thread it continues ("" = a new one)
    title: str = ""
    model_status: str = "open"
    reason: str = ""


def _compatible(a: Moment, b: Moment) -> bool:
    return thread_match([a], {"anchors": " ".join(anchors_of([b]))}) or thread_match([b], {"anchors": " ".join(anchors_of([a]))})


def split_incompatible(moments: "Sequence[Moment]") -> "list[list[Moment]]":
    """The model may LINK moments into a thread, never glue strangers together: the groups of ``moments`` that are connected by ``thread_match`` (the same named person, or a
    shared matter word). A group the model drew across people and matters ("dentist" + "knee" + a colleague's move) falls apart here, and each part is its own thread."""
    parts: "list[list[Moment]]" = []
    for m in moments:
        hit = [p for p in parts if any(_compatible(m, x) for x in p)]
        if not hit:
            parts.append([m])
            continue
        merged = [m] + [x for p in hit for x in p]
        parts = [p for p in parts if p not in hit] + [merged]
    return parts


def deterministic_groups(moments: "Sequence[Moment]") -> "list[Group]":
    """The no-model THREADS step (and the fallback for any moment the model left out): moments about the same named person, or sharing two distinctive
    words, are one thread."""
    groups: "list[Group]" = []
    for m in moments:
        for g in groups:
            if thread_match(g.moments, {"anchors": " ".join(anchors_of(g.moments))}):
                if thread_match([m], {"anchors": " ".join(anchors_of(g.moments))}):
                    g.moments.append(m)
                    break
        else:
            groups.append(Group([m], title=" ".join(m.quote.split()[:8])))
    return groups


def apply_threads(raw: str, moments: "list[Moment]", threads: "list[dict]", counts: "dict[str, int]") -> "Optional[list[Group]]":
    """The model's THREADS reply applied to the verified moments. ``None`` = unusable JSON. An operation that names a moment or a thread that does not
    exist, or updates the same thread twice, is DROPPED (counted); a moment the reply never places is kept (grouped by code): an omission loses nothing."""
    got = _reply_object(raw, "threads", counts)
    if got is None or not isinstance(got.get("threads", []), list):
        return None
    by_mid = {m.mid: m for m in moments}
    by_tid = {t["id"]: t for t in threads}
    placed: "set[str]" = set()
    updated: "set[str]" = set()
    groups: "list[Group]" = []
    for op in got.get("threads") or []:
        if not isinstance(op, dict):
            counts["ops_dropped"] += 1
            continue
        mids = [str(x).strip() for x in (op.get("moments") or []) if isinstance(op.get("moments"), list)]
        ms = [by_mid[x] for x in mids if x in by_mid and x not in placed]
        reason = _sq(op.get("reason"))[:200]
        if not ms or not reason:                                 # a mandatory reason, and something to place
            counts["ops_dropped"] += 1
            continue
        kind = str(op.get("op") or "").lower()
        status = str(op.get("status") or "open").lower()
        if kind == "update":
            tid = str(op.get("thread") or "").strip()
            if tid not in by_tid or tid in updated:
                counts["ops_dropped"] += 1
                continue
            updated.add(tid)
            parts = split_incompatible(ms)
            groups.append(Group(parts[0], thread_id=tid, model_status=status, reason=reason))
            groups += [Group(p, model_status=status, reason=reason) for p in parts[1:]]
        elif kind == "create":
            title = " ".join(_sq(op.get("title")).split()[:8])
            parts = split_incompatible(ms)
            groups += [Group(p, title=title if len(parts) == 1 and title else " ".join(p[0].quote.split()[:8]), model_status=status, reason=reason) for p in parts]
            if len(parts) > 1:
                counts["groups_split"] += 1
        else:
            counts["ops_dropped"] += 1
            continue
        placed |= {m.mid for m in ms}
        counts["ops_applied"] += 1
    rest = [m for m in moments if m.mid not in placed]
    groups += deterministic_groups(rest)
    return groups


def _people_words(whos: "Iterable[Iterable[str]]") -> "set[str]":
    """The lower-cased words of the people the model named (``who``) across a thread's moments: who the story is about, never what it is about."""
    return {w.lower() for who in whos for name in who for w in re.findall(r"[A-Za-z]{3,}", str(name))}


def _matter_words(quote: str, people: "Iterable[str]" = ()) -> "set[str]":
    """The words that say WHAT a line is about: its words without the stop words, the generic ones and the PEOPLE in the thread (a person is who, not what). A named place or organisation
    stays: 'We fly to Cornwall on the 3rd' ... 'Cornwall was lovely' is one trip."""
    gone = {w.lower() for w in people}
    return {w for w in _words(quote) if w not in _ANCHOR_STOP and w not in gone}


def finishes(current: "Sequence[Moment]", model_status: str, old_rows: "Sequence[dict]" = ()) -> bool:
    """Does this thread END tonight? A moment must say a thing finished (``later: done``) AND a second reading must agree: either the THREADS call calls the thread ``resolved``, or the thread
    holds a dated PLAN (kind ``plan``, still open) said before the finish AND the finishing line is about that plan's matter (it shares a word with it that is not a name) - 'the quiz night is on the
    20th' ... 'the quiz night went really well'. Not every ``done`` is a finish:
    * a habit's single occasion ('Jorunn and I ran 5k today', later=done) is a finished EVENT, not a finished story (measured, K9: every run and garden line carried ``done`` and both threads were closed);
    * a ``change`` moment ('the trip is off, we are going elsewhere') is the plan CHANGING, not finishing (measured, 4B, 2026-10-10: it carried ``done`` and the plan it replaced was closed instead of
      being 'changed' - K9);
    * a line about something else in the same story ('Sorrel practised the cello for an hour today', done) does not finish the concert plan that shares only the child's name (measured, K10: the
      benign thread was closed on its second mention and could never be raised)."""
    done = [x for x in current if x.later == "done" and x.kind != "change"]
    if not done:
        return False
    if model_status == "resolved":
        return True
    at = max(x.turn.at for x in done)
    people = _people_words([x.who for x in current] + [str(o.get("who") or "").split(",") for o in old_rows])
    finish_words = set().union(*[_matter_words(x.quote, people) for x in done])
    plans = [x for x in current if x.kind == "plan" and x.later == "open" and x.turn.at < at]
    if any(_matter_words(x.quote, people) & finish_words for x in plans):
        return True
    return any(o.get("kind") == "plan" and o.get("later") == "open" and float(o.get("said_at") or 0) < at.timestamp()
               and _matter_words(str(o.get("quote") or ""), people) & finish_words for o in old_rows)


def merge_status(a: str, b: str) -> str:
    """Two groups of one thread merged: what either reading asserted survives (finished > changed > open)."""
    for s in ("resolved", "changed"):
        if s in (a, b):
            return s
    return "open"


def merge_groups(groups: "list[Group]") -> "list[Group]":
    """ONE THREAD PER PERSON-OR-MATTER, enforced by code: two groups tonight that ``thread_match`` each other (the same named person; or, for nameless
    remarks, a shared matter word) are one thread, whatever the model grouped. A group already tied to a stored thread keeps that thread."""
    out: "list[Group]" = []
    for g in groups:
        for h in out:
            a = {"anchors": " ".join(anchors_of(h.moments))}
            b = {"anchors": " ".join(anchors_of(g.moments))}
            if (not g.thread_id or not h.thread_id or g.thread_id == h.thread_id) and (thread_match(g.moments, a) or thread_match(h.moments, b)):
                h.moments += g.moments
                h.thread_id = h.thread_id or g.thread_id
                h.model_status = merge_status(h.model_status, g.model_status)
                break
        else:
            out.append(Group(list(g.moments), g.thread_id, g.title, g.model_status, g.reason))
    return out


def link_existing(groups: "list[Group]", threads: "list[dict]") -> None:
    """PREFER UPDATE OVER CREATE, enforced by code: a group the model created (or the code grouped) that continues a stored thread - a leave thread the
    model was never shown included - joins it. A group never joins a thread another group already took tonight."""
    taken = {g.thread_id for g in groups if g.thread_id}
    for g in groups:
        if g.thread_id:
            continue
        for t in threads:
            if t["id"] not in taken and t["status"] != "retracted" and thread_match(g.moments, t):
                g.thread_id = t["id"]
                taken.add(t["id"])
                break


# ═══ stage 4: decide ══════════════════════════════════════════════════════════════════════════════════════════════════════

def _date(s: str) -> "Optional[_dt.date]":
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def quiet_threshold(days: "Sequence[str]") -> int:
    """How many days without a mention make an open thread quiet: at least ``QUIET_AFTER_DAYS``, or twice the thread's own usual gap between mentions
    (a story mentioned every fortnight is not quiet after ten days). Pure."""
    ds = sorted({d for d in (_date(x) for x in days) if d})
    if len(ds) < 2:
        return QUIET_AFTER_DAYS
    gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
    return max(QUIET_AFTER_DAYS, int(2 * sum(gaps) / len(gaps)))


def significant(t: dict) -> bool:
    """A thread worth serving: mentioned on two or more days, or heavy (weight 3), or one that changed or finished. A lone minor remark is a fact for the
    memory store, not a story. Pure."""
    return int(t.get("mentions_n") or 0) >= 2 or int(t.get("weight_max") or 1) >= 3 or t.get("status") in ("changed", "resolved")


def salience(t: dict, today: "_dt.date") -> float:
    """``weight`` x recency decay x the open factor, the shape of ``proactive.selector.salience``; computed when read, never stored."""
    last = _date(t.get("last_day") or "") or today
    age = max(0, (today - last).days)
    factor = {"open": 1.0, "changed": 0.9, "recurring": 0.8, "quiet": 0.3, "resolved": 0.0}.get(str(t.get("status")), 0.5)
    return float(t.get("weight_max") or 1) * (0.5 ** (age / 3.0)) * factor


def leave_reason(t: dict, quotes: "Sequence[str]", today: "_dt.date", ledger_rows: "Sequence[dict]" = ()) -> str:
    """Why this thread must not be raised unprompted ('' = it may). A deterministic floor: the model cannot override it."""
    topic = topic_of(quotes) or str(t.get("topic") or "")
    if topic == "owner_said_no":
        return "owner_said_no"
    if topic in ("health", "grief", "money", "conflict"):
        return topic
    anchors = set(str(t.get("anchors") or "").split())
    ignored = int(t.get("ignored_raises") or 0) + sum(
        1 for r in ledger_rows or () if str(r.get("outcome") or "") in ("ignored", "undelivered") and anchors & set(_words(str(r.get("cue_words") or ""))))
    if ignored >= 2:
        return "ignored_twice"
    if t.get("last_feeling") in NEGATIVE and int(t.get("weight_max") or 1) >= 3 and t.get("last_day") == today.isoformat():
        return "too_fresh"
    return ""


def decide_raise(threads: "list[dict]", quotes_by_thread: "dict[str, list[str]]", today: "_dt.date", ledger_rows: "Sequence[dict]" = ()) -> None:
    """Set ``raise_policy`` / ``leave_reason`` on every thread, in place: ``leave`` per the floor above; ``raise`` for AT MOST ONE open thread
    (weight >= 2, due, recent, not raised in the last 24 h); everything else ``wait``. Under the ``restraint`` fault (the bench's control) everything
    open is raised."""
    best, best_s = None, 0.0
    for t in threads:
        why = leave_reason(t, quotes_by_thread.get(t["id"], []), today, ledger_rows)
        t["leave_reason"] = why
        t["raise_policy"] = "leave" if why else "wait"
        if fault("restraint") and t["status"] in ("open", "changed"):
            t["raise_policy"], t["leave_reason"] = "raise", ""
            continue
        if why or t["status"] not in ("open", "changed", "recurring") or int(t.get("weight_max") or 1) < 2:
            continue
        due = _date(t.get("next_raise_after") or "")
        if due is not None and due > today:
            continue
        raised = _date(t.get("last_raised_at") or "")
        if raised is not None and (today - raised).days < 1:
            continue
        s = salience(t, today)
        if (_date(t.get("last_day") or "") or today) < today - _dt.timedelta(days=MORNING_DAYS):
            continue
        if s > best_s:
            best, best_s = t, s
    if best is not None and not fault("restraint"):
        best["raise_policy"] = "raise"


def note_raise(t: dict, today: "_dt.date", *, ignored: bool = False) -> None:
    """A raise happened on ``today`` (``ignored`` = the member did not take it up): the back-off doubles after each ignored raise (1, 2, 4, 8 days)."""
    t["last_raised_at"] = today.isoformat()
    if ignored:
        t["ignored_raises"] = int(t.get("ignored_raises") or 0) + 1
    gap = 2 ** int(t.get("ignored_raises") or 0)
    t["next_raise_after"] = (today + _dt.timedelta(days=gap)).isoformat()


def plan_mornings(threads: "list[dict]", quotes_by_thread: "dict[str, list[str]]", start: "_dt.date", days: int, *, ignore_all: bool = True) -> "list[dict]":
    """Simulate ``days`` consecutive mornings against the stored threads (the restraint cell K10): each morning ``decide_raise`` picks at most one thread,
    the member ignores it (or not), the back-off moves. Returns ``[{"day", "raised": thread id | None, "title"}]``. Pure over copies."""
    ts = [dict(t) for t in threads]
    out = []
    for d in range(days):
        today = start + _dt.timedelta(days=d)
        for t in ts:
            t["last_day"] = max(t.get("last_day") or "", (today - _dt.timedelta(days=1)).isoformat()) if t["status"] == "open" else t.get("last_day")
        decide_raise(ts, quotes_by_thread, today)
        pick = next((t for t in ts if t["raise_policy"] == "raise"), None)
        if fault("restraint"):                                    # the control raises every open thread: more than one a morning
            picks = [t for t in ts if t["raise_policy"] == "raise"]
        else:
            picks = [pick] if pick else []
        for t in picks:
            note_raise(t, today, ignored=ignore_all)
        out.append({"day": today.isoformat(), "raised": [t["id"] for t in picks], "titles": [t["title"] for t in picks]})
    return out


def mood_line(obs: "Sequence[dict]", today: "_dt.date") -> str:
    """A template sentence over COUNTS ('' when there is too little to say): of the last 7 days on which the owner voiced a feeling, how many held a
    worry. Never a model's reading; computed only for a member the household affect policy allows."""
    days: "dict[str, int]" = {}
    for o in obs:
        d = _date(o.get("day") or "")
        if d is None or (today - d).days > 7 or o.get("state") != "current" or not int(o.get("valence") or 0):
            continue
        days[o["day"]] = min(days.get(o["day"], 0), int(o["valence"]))
    if len(days) < 3:
        return ""
    neg = sum(1 for v in days.values() if v < 0)
    return f"{neg} of the last {len(days)} days they spoke about a feeling included something that weighed on them." if neg else ""


# ═══ the pass ═════════════════════════════════════════════════════════════════════════════════════════════════════════════

def _oid(user_id: str, turn_id: str, quote: str) -> str:
    return "no-" + hashlib.sha1(f"{user_id}|{turn_id}|{quote.lower()}".encode()).hexdigest()[:20]


def _tid(user_id: str, first_turn: str, quote: str) -> str:
    return "nt-" + hashlib.sha1(f"{user_id}|{first_turn}|{quote.lower()}".encode()).hexdigest()[:16]


async def _affect_allowed(svc: Any, user_id: str) -> bool:
    try:
        return bool(svc is not None and await svc._affect_allowed(user_id))
    except Exception:  # noqa: BLE001 - an unknown lookup refuses (the policy's own rule)
        return False


async def _opted_out(user_id: str) -> bool:
    try:
        import memory_service

        return bool(await memory_service._user_opted_out(user_id))
    except Exception:  # noqa: BLE001 - the digest's own opt-out wall is the first one
        return False


async def _ledger_rows(user_id: str, now: "_dt.datetime") -> "list[dict]":
    """The member's recent closed deliveries (proactive ledger, flag-dark): ``[]`` when it is off, unreachable or empty. Reads rows only."""
    try:
        from proactive import ledger

        if not ledger.ledger_enabled():
            return []
        from db_compat import get_compat_db

        since = (now - _dt.timedelta(days=14)).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with get_compat_db() as db:
            async with db.execute("SELECT kind, cue_words, outcome FROM proactive_deliveries WHERE user_id = ? AND surfaced_at >= ? "
                                  "AND outcome IS NOT NULL LIMIT 100", (user_id, since)) as cur:
                return [{"kind": r[0], "cue_words": r[1], "outcome": r[2]} for r in await cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("night_mind: ledger read skipped (%s)", type(exc).__name__)
        return []


async def run_for_user(user_id: str, transcript: Any, svc: Any = None, *, now: "Optional[_dt.datetime]" = None, force_mode: "Optional[str]" = None,
                       cfg: "Optional[Config]" = None, ledger_rows: "Optional[Sequence[dict]]" = None, night_date: str = "") -> "dict[str, Any]":
    """The night's reflection for ONE member. Returns counters only (never text). Never raises: a failure is ``status: error`` with the exception class.
    ``force_mode`` overrides the env (the CLI and the bench); ``cfg`` points the calls at another server / context size."""
    m = force_mode or mode()
    res: "dict[str, Any]" = {"status": "off", "mode": m, "user_id": user_id}
    if m == "off":
        return res
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cfg = cfg or _CONFIG or config_from_env()
    t0 = time.monotonic()
    try:
        return await _run(user_id, transcript, svc, m, now, cfg, ledger_rows, night_date or local_day(now), res)
    except ModelUnreachable as exc:
        if isinstance(exc, ModelTimeout):
            logger.warning("NIGHT_MIND user=%s status=llm_timeout budget_s=%s prompt_tokens=%d max_tokens=%d decode_tok_s=%g prefill_tok_s=%g (nothing written)",
                           user_id, exc.budget_s, exc.prompt_tokens, exc.max_tokens, exc.decode_tok_s, exc.prefill_tok_s)
            res.update(llm_timeout=True, timeout_budget_s=exc.budget_s)
        else:
            logger.warning("NIGHT_MIND user=%s status=llm_unreachable kind=%s (nothing written)", user_id, exc)
        res.update(exc.counts)                                   # what the abandoned night spent: calls made (the failed one included), tokens
        res.update(status="llm_unreachable", error=str(exc), written=0)
        return res
    except Exception as exc:  # noqa: BLE001 - the digest must go on
        logger.warning("NIGHT_MIND user=%s status=error kind=%s", user_id, type(exc).__name__)
        res.update(status="error", error=type(exc).__name__)
        return res
    finally:
        res["wall_s"] = round(time.monotonic() - t0, 2)


COUNT_KEYS = ("turns_in", "turns_dropped_routine", "turns_skipped_cap", "chunks", "calls", "moments_calls", "threads_calls", "calls_invalid", "calls_salvaged", "tail_calls", "tail_lost",
              "moments_proposed", "moments_dropped_id", "moments_dropped_quote", "moments_verified", "moments_held", "moments_history", "ops_applied",
              "ops_dropped", "groups_split", "observations_written", "observations_pending", "threads_created", "threads_updated", "threads_resolved", "threads_quiet", "quiet_vetoed",
              "prompt_tokens", "completion_tokens", "prompt_tokens_est_max")


async def _run(user_id: str, transcript: Any, svc: Any, m: str, now: "_dt.datetime", cfg: Config, ledger_rows: Any, night_date: str,
               res: "dict[str, Any]") -> "dict[str, Any]":
    started = time.monotonic()
    counts: "dict[str, int]" = {k: 0 for k in COUNT_KEYS}
    res.update(status="ran", **counts, written=0, capped=False, partial=False, changes=[], ctx_tokens=cfg.ctx_tokens, chunk_budget=cfg.chunk_budget,
               max_calls=cfg.max_calls)
    turns = turns_of(transcript, now)
    counts["turns_in"] = len(turns)
    if sum(len(t.text.split()) for t in turns) < MIN_WORDS or len(turns) < MIN_TURNS:
        return _finish(res, counts, status="skipped", skipped_reason="quiet_day")
    if await _opted_out(user_id):
        return _finish(res, counts, status="skipped", skipped_reason="opted_out")
    chunks, pstats = pack(turns, cfg)
    counts.update(pstats)
    if sum(len(c) for c in chunks) < MIN_TURNS and not fault("chunking"):
        return _finish(res, counts, status="skipped", skipped_reason="quiet_day")
    counts["chunks"] = len(chunks)
    ok, why = await probe_model(cfg)
    if not ok:
        raise ModelUnreachable(why)
    usage = {"prompt_tokens": 0, "completion_tokens": 0}

    async def call(messages: list, max_tokens: int, schema: "Optional[dict]" = None) -> str:
        counts["calls"] += 1
        counts["prompt_tokens_est_max"] = max(counts["prompt_tokens_est_max"], sum(est_tokens(x["content"]) for x in messages))
        try:
            reply = await _complete(messages, max_tokens, cfg, usage, schema=schema)
        except ModelUnreachable as exc:
            exc.counts = {**counts, **usage}
            _trace({"kind": "call", "n": counts["calls"], "messages": messages, "max_tokens": max_tokens, "error": type(exc).__name__})
            raise
        _trace({"kind": "call", "n": counts["calls"], "messages": messages, "max_tokens": max_tokens, "reply": reply})
        return reply

    # ── stage 2: MOMENTS (one call per chunk) ─────────────────────────────────────────────────────────────────────
    moments: "list[Moment]" = []
    for n, chunk in enumerate([] if fault("echo") else chunks):
        part = chunk
        for attempt in (0, 1):                                    # attempt 1 = the TAIL of this chunk the model never got to, asked once, only if a call is to spare
            prompt = MOMENTS_USER.format(lines="\n".join(_line(f"m{i}", t) for i, t in enumerate(part, 1)), cap=MAX_MOMENTS_PER_CHUNK)
            salvaged, proposed = counts["calls_salvaged"], counts["moments_proposed"]
            raw = await call([{"role": "system", "content": MOMENTS_SYSTEM}, {"role": "user", "content": prompt}], MOMENT_MAX_TOKENS, _MOMENT_SCHEMA)
            counts["moments_calls"] += 1
            got = parse_moments(raw, part, n, counts)
            if got is None:
                counts["calls_invalid"] += 1
                res["partial"] = True
                break
            moments += got
            # the model lists moments in line order and stops at its cap (or is cut off): what it never got to is the NEWEST part of the day - a change, a finish, the story that
            # kept going - so the tail is asked for once more rather than silently lost (measured: the 4B stopped at 8 of 12-13 lines, and "quiet" / "changed" read the old half)
            stopped_short = counts["calls_salvaged"] > salvaged or counts["moments_proposed"] - proposed >= MAX_MOMENTS_PER_CHUNK
            tail = unreached_tail(part, got) if stopped_short else []
            spare = counts["calls"] + (len(chunks) - n - 1) + 2 <= cfg.max_calls        # this call + every later chunk's + the THREADS call stay inside the cap
            if not tail or attempt or not spare or len(moments) >= MAX_MOMENTS_TO_THREADS:
                counts["tail_lost"] += len(tail)
                break
            counts["tail_calls"] += 1
            part = tail
    if fault("echo"):                                            # the bench's copying reflection: every turn is a moment, no model, no selection
        moments = [Moment(turn=t, quote=_sq(t.text)) for c in chunks for t in c]
        counts["moments_proposed"] = len(moments)
    seen: "set[tuple]" = set()
    uniq: "list[Moment]" = []
    for mo in sorted(moments, key=lambda x: (x.turn.at if x.turn else now, -x.weight)):
        key = (mo.turn.id if mo.turn else "", mo.quote.lower())
        if key not in seen:
            seen.add(key)
            uniq.append(mo)
    moments = uniq
    allowed = await _affect_allowed(svc, user_id) if svc is not None else True
    for mo in moments:
        if not allowed:
            mo.feeling = "none"                                   # the household affect policy: no feeling is kept for a member it does not cover
        judge_moment(mo)
    if svc is not None:
        counts["moments_history"] = await mark_replaced(svc, user_id, moments)
    counts["moments_verified"] = sum(1 for mo in moments if mo.state in ("current", "history"))
    counts["moments_held"] = sum(1 for mo in moments if mo.state == "held")
    # the model sees only moments the gate did not hold (a held quote is not a belief), at most 24, heaviest first
    live = sorted((mo for mo in moments if mo.state != "held"), key=lambda x: (-x.weight, x.turn.at if x.turn else now))[:MAX_MOMENTS_TO_THREADS]
    live.sort(key=lambda x: (x.turn.at if x.turn else now))
    for i, mo in enumerate(live, 1):
        mo.mid = f"q{i}"

    # ── stage 3: THREADS (one call per member) ────────────────────────────────────────────────────────────────────
    backend = night_store.get_backend()
    threads = await backend.threads(user_id)
    all_prior = await backend.observations(user_id)
    old_obs = [o for o in all_prior if o["state"] == "current"]
    known_ids = {o["id"] for o in all_prior}                        # a re-run of the same night finds its own rows: they are not new mentions
    newest: "dict[str, str]" = {}
    for o in old_obs:
        newest[o["thread_id"]] = o["quote"]
    today = _date(night_date) or now.astimezone(_local_tz()).date()
    listed = [t for t in threads if t["status"] in ("open", "changed", "recurring") and not t["leave_reason"]][:MAX_OPEN_THREADS_LISTED]   # leave threads are never named in a prompt
    if live and (len(live) >= 2 or listed) and counts["calls"] < cfg.max_calls and not fault("no_threads_call"):
        prompt = THREADS_USER.format(old=_render_old(listed, newest), new=_render_new(live))
        raw = await call([{"role": "system", "content": THREADS_SYSTEM}, {"role": "user", "content": prompt}], THREAD_MAX_TOKENS)
        counts["threads_calls"] += 1
        groups = apply_threads(raw, live, listed, counts)
        if groups is None:
            counts["calls_invalid"] += 1
            res["partial"] = True
            groups = deterministic_groups(live)
    else:
        groups = deterministic_groups(live)
    groups = merge_groups(groups)
    link_existing(groups, threads)
    res["capped"] = counts["calls"] >= cfg.max_calls and (bool(counts["turns_skipped_cap"]) or len(chunks) >= cfg.max_calls)

    # ── stage 4: DECIDE (code) ────────────────────────────────────────────────────────────────────────────────────
    ledger = list(ledger_rows) if ledger_rows is not None else await _ledger_rows(user_id, now)
    plan = _build_plan(user_id, groups, threads, old_obs, today, now, ledger, night_date, counts, [mo for mo in moments if mo.state == "held"], known_ids, turns)
    res["changes"] = plan["changes"]
    if _TRACE is not None:
        _trace({"kind": "plan", "night_date": night_date, "today": today.isoformat(),
                "groups": [{"thread": g.thread_id, "title": g.title, "model_status": g.model_status, "reason": g.reason,
                            "moments": [{"mid": x.mid, "turn": x.turn.id, "day": x.day, "kind": x.kind, "state": x.state, "quote": x.quote} for x in g.moments]} for g in groups],
                "threads": [{k: t.get(k) for k in ("id", "title", "status", "first_day", "last_day", "mentions_n", "weight_max", "anchors", "topic", "policy", "leave_reason")} for t in plan["threads"]],
                "mention_days": plan["mention_days"], "changes": plan["changes"]})
    mood = mood_line(plan["all_obs"], today) if allowed else ""
    res["mood"] = bool(mood)
    counts["observations_written"] = sum(1 for o in plan["new_obs"] if o["state"] != "held")
    counts["observations_pending"] = sum(1 for o in plan["new_obs"] if o["state"] == "held")
    counts["prompt_tokens"], counts["completion_tokens"] = usage["prompt_tokens"], usage["completion_tokens"]
    res.update(counts)
    if m != "enforce":                                             # shadow: every verdict above is the measurement, nothing is written
        res["written"] = 0
        _log(res)
        return res
    # ── commit: the whole night, at the end - ONE transaction, under the user's erase lock, after a last look at the forget ledger ──
    run = night_store.run_row(
        id="nr-" + hashlib.sha1(f"{user_id}|{night_date}|{time.time()}".encode()).hexdigest()[:16], user_id=user_id, night_date=night_date,
        model_id=cfg.model[:64], prompt_sha=prompt_sha(), schema_sha=schema_sha(), watermark_msg_id=turns[-1].id if turns else "",
        status="partial" if res["partial"] else "ok", wall_s=0.0, counts={k: res[k] for k in COUNT_KEYS}, created_at=time.time())
    async with _user_lock(user_id):
        if _DELETED_AT.get(user_id, -1.0) >= started:            # the member was erased while the model calls ran: nothing of this pass comes back
            res["written"] = 0
            return _finish(res, counts, status="skipped", skipped_reason="erased_during_run")
        try:
            new_obs, changed_obs, thread_rows = await _unforgotten_plan(user_id, plan, threads)
        except Exception as exc:  # noqa: BLE001 - a forget that cannot be checked is a forget that cannot be trusted: write nothing, the next pass retries
            logger.warning("night_mind: forget check failed for %s (%s) - nothing written", user_id, type(exc).__name__)
            res["written"] = 0
            return _finish(res, counts, status="skipped", skipped_reason="forget_check_failed")
        await backend.commit_plan(new_obs + changed_obs, thread_rows, run)
    res["written"] = len(new_obs) + len(changed_obs)
    invalidate(user_id)
    _log(res)
    return res


async def _unforgotten_plan(user_id: str, plan: "dict[str, Any]", prior_threads: "list[dict]") -> "tuple[list[dict], list[dict], list[dict]]":
    """The plan's rows minus anything that now names an entity the owner asked Zoe to forget (the durable ledger, read AFTER the model calls: an
    erasure that completed while they ran must not be written back). A new thread left with no observation is dropped. RAISES if the ledger cannot be read."""
    import memory_forgotten

    new_obs, _ = await memory_forgotten.keep_unforgotten(user_id, plan["new_obs"], text_of=lambda o: o["quote"])
    changed_obs, _ = await memory_forgotten.keep_unforgotten(user_id, plan["changed_obs"], text_of=lambda o: o["quote"])
    threads, _ = await memory_forgotten.keep_unforgotten(user_id, plan["threads"], text_of=lambda t: t["title"])
    existing = {t["id"] for t in prior_threads}
    alive = {o["thread_id"] for o in new_obs + changed_obs}
    return new_obs, changed_obs, [t for t in threads if t["id"] in existing or t["id"] in alive]


def _finish(res: "dict[str, Any]", counts: "dict[str, int]", **kw: Any) -> "dict[str, Any]":
    res.update(counts)
    res.update(kw)
    return res


def _log(res: "dict[str, Any]") -> None:
    logger.info("NIGHT_MIND user=%s mode=%s turns=%d routine=%d chunks=%d calls=%d moments=%d/%d held=%d history=%d threads+%d/~%d written=%d pending=%d "
                "invalid=%d", res.get("user_id"), res.get("mode"), res.get("turns_in", 0), res.get("turns_dropped_routine", 0), res.get("chunks", 0),
                res.get("calls", 0), res.get("moments_verified", 0), res.get("moments_proposed", 0), res.get("moments_held", 0), res.get("moments_history", 0),
                res.get("threads_created", 0), res.get("threads_updated", 0), res.get("observations_written", 0), res.get("observations_pending", 0),
                res.get("calls_invalid", 0))


def _build_plan(user_id: str, groups: "list[Group]", threads: "list[dict]", old_obs: "list[dict]", today: "_dt.date", now: "_dt.datetime",
                ledger: "Sequence[dict]", night_date: str, counts: "dict[str, int]", held: "Sequence[Moment]" = (), known_ids: "frozenset[str] | set[str]" = frozenset(),
                raw_turns: "Sequence[Turn]" = ()) -> "dict[str, Any]":
    """Stage 4 as a pure plan: the thread rows and observation rows the night would write (nothing is written here)."""
    by_id = {t["id"]: dict(t) for t in threads}
    new_obs: "list[dict]" = []
    changed: "dict[str, dict]" = {}
    changes: "list[dict]" = []
    touched: "set[str]" = set()
    for g in groups:
        ms = sorted(g.moments, key=lambda x: (x.turn.at, x.mid))
        if g.thread_id and g.thread_id in by_id:
            t = by_id[g.thread_id]
            counts["threads_updated"] += 1
            before_status = t["status"]
        else:
            first = ms[0]
            tid = _tid(user_id, first.turn.id, first.quote)
            t = night_store.thread_row(id=tid, user_id=user_id, title=g.title or " ".join(first.quote.split()[:8]), first_day=first.day, last_day=first.day,
                                       source_ref=f"night_threads:{tid}", opened_run=night_date)
            by_id[tid] = t
            counts["threads_created"] += 1
            before_status = ""
            changes.append({"type": "new", "thread": tid, "ids": [x.turn.id for x in ms]})
        touched.add(t["id"])
        live = list(ms)
        anchors = set(str(t["anchors"]).split()) | anchors_of(ms, t["title"])
        t["anchors"] = " ".join(sorted(anchors))[:600]
        t["topic"] = topic_of([x.quote for x in ms] + [t["title"]]) or t["topic"]
        if live:
            t["first_day"] = min([d for d in (t["first_day"], min(x.day for x in live)) if d])
            t["last_day"] = max(t["last_day"], max(x.day for x in live))
            t["weight_max"] = max(int(t["weight_max"]), max(x.weight for x in live))
            fresh = [x for x in live if _oid(user_id, x.turn.id, x.quote) not in known_ids]
            t["mentions_n"] = int(t["mentions_n"]) + len(fresh)
            feel = [x for x in live if x.feeling != "none"]
            if feel:
                t["last_feeling"] = feel[-1].feeling
        cur = [x for x in live if x.state == "current"]
        done = finishes(cur, g.model_status, [o for o in old_obs if o["thread_id"] == t["id"]])
        changed_kind = any(x.kind == "change" and moment_change_backed(x) for x in cur)   # the model's label alone never changes a thread
        new_status = t["status"] if before_status else "open"
        if done:
            new_status = "resolved"
        elif changed_kind:
            new_status = "changed"
        elif t["status"] in ("quiet", "resolved") and any(_oid(user_id, x.turn.id, x.quote) not in known_ids for x in live):
            new_status = "open"                                     # mentioned again TONIGHT (a re-run of the same night finds only its own rows): back in play
        if before_status and new_status != before_status:
            changes.append({"type": "resolved" if new_status == "resolved" else "advanced", "thread": t["id"], "ids": [x.turn.id for x in live][:3]})
        elif before_status and live and any(_oid(user_id, x.turn.id, x.quote) not in known_ids for x in live):
            changes.append({"type": "advanced", "thread": t["id"], "ids": [x.turn.id for x in live][:3]})
        if new_status == "resolved" and before_status != "resolved":
            counts["threads_resolved"] += 1
            t["closed_run"] = night_date
        t["status"] = new_status
        for x in ms:
            state = x.state
            valid_to = now.timestamp() if state == "history" else None
            row = night_store.obs_row(
                id=_oid(user_id, x.turn.id, x.quote), user_id=user_id, thread_id=t["id"], turn_id=x.turn.id, quote=x.quote, kind=x.kind,
                who=",".join(x.who), feeling=x.feeling, valence=x.valence, weight=x.weight, later=x.later, day=x.day, said_at=x.turn.at.timestamp(),
                valid_from=x.turn.at.timestamp(), valid_to=valid_to, state=state, basis=x.basis or x.why_held,
                authority_class="user_stated_derived" if state != "held" else "pending", run_id=night_date)
            new_obs.append(row)
        # a CHANGE moment (or a resolution) on the thread retires the thread's EARLIER current observations: history, with a validity end, never deleted
        # (a ``done`` line retires the earlier ones only when it FINISHED the thread - ``finishes()`` above - not when it is an unrelated line that carries ``later: done``)
        cut = max((x.turn.at for x in live if (x.kind == "change" and moment_change_backed(x)) or (done and x.later == "done" and x.kind != "change")), default=None)
        if cut is not None:
            keep_ids = {_oid(user_id, x.turn.id, x.quote) for x in live if x.turn.at >= cut}
            for o in old_obs:
                if o["thread_id"] == t["id"] and o["state"] == "current" and o["said_at"] < cut.timestamp() and o["id"] not in keep_ids:
                    changed[o["id"]] = {**o, "state": "history", "valid_to": cut.timestamp()}
            for row in new_obs:
                if row["thread_id"] == t["id"] and row["state"] == "current" and row["said_at"] < cut.timestamp():
                    row["state"], row["valid_to"] = "history", cut.timestamp()
    # held quotes (hedged / unsupported / uncited): pending candidates, never served, on no thread; at most MAX_HELD_ROWS a night
    for x in list(held)[:MAX_HELD_ROWS]:
        new_obs.append(night_store.obs_row(
            id=_oid(user_id, x.turn.id, x.quote), user_id=user_id, thread_id="", turn_id=x.turn.id, quote=x.quote, kind=x.kind, who=",".join(x.who),
            feeling=x.feeling, valence=x.valence, weight=x.weight, later=x.later, day=x.day, said_at=x.turn.at.timestamp(), valid_from=x.turn.at.timestamp(),
            state="held", basis=x.why_held, authority_class="pending", run_id=night_date))
    all_obs = [dict(o) for o in old_obs if o["id"] not in changed] + list(changed.values()) + [dict(o) for o in new_obs]
    by_thread_days: "dict[str, list[str]]" = {}
    by_thread_ids: "dict[str, list[str]]" = {}
    for o in sorted(all_obs, key=lambda r: r["said_at"]):
        if o["thread_id"] and o["state"] != "held":
            if o["day"] not in by_thread_days.setdefault(o["thread_id"], []):
                by_thread_days[o["thread_id"]].append(o["day"])
            if o["turn_id"] not in by_thread_ids.setdefault(o["thread_id"], []):
                by_thread_ids[o["thread_id"]].append(o["turn_id"])
    # QUIET (code): an open thread not mentioned for longer than its own cadence allows (at least QUIET_AFTER_DAYS, or twice its usual gap) - absence is a
    # fact about counts, never a closing (``absence`` is the bench's fault: it closes every open thread tonight's moments did not mention)
    for t in by_id.values():
        if t["status"] not in ("open", "recurring") and not (fault("absence") and t["status"] == "changed"):
            continue
        if fault("absence"):                                      # absence is contradiction: not mentioned TODAY = over
            if t["last_day"] < today.isoformat():
                t["status"], t["closed_run"] = "resolved", night_date
            continue
        last = _date(t["last_day"])
        if last is not None and (today - last).days >= quiet_threshold(by_thread_days.get(t["id"], [])):
            if raw_turns and spoken_since(t["title"], [o["quote"] for o in all_obs if o["thread_id"] == t["id"] and o["state"] != "held"], raw_turns, t["last_day"], today):
                counts["quiet_vetoed"] += 1         # the owner's own later turn names it: the model dropped that mention, the thread is not abandoned
                continue
            t["status"] = "quiet"
            counts["threads_quiet"] += 1
            changes.append({"type": "quiet", "thread": t["id"], "ids": by_thread_ids.get(t["id"], [])[-2:]})
    if fault("notice"):                                         # the bench's always-notice reflection: every thread, every night
        known = {c["thread"] for c in changes if c["type"] == "quiet"}
        changes += [{"type": "quiet", "thread": t["id"], "ids": by_thread_ids.get(t["id"], [])[-2:]} for t in by_id.values() if t["id"] not in known]
    # the raise / leave floor over every thread
    quotes: "dict[str, list[str]]" = {}
    for o in all_obs:
        quotes.setdefault(o["thread_id"], []).append(o["quote"])
    tl = list(by_id.values())
    decide_raise(tl, quotes, today, ledger)
    return {"threads": tl, "new_obs": new_obs, "changed_obs": list(changed.values()), "changes": changes, "all_obs": all_obs, "mention_days": by_thread_days}


# ═══ readers ══════════════════════════════════════════════════════════════════════════════════════════════════════════════

_CHECKIN_RE = re.compile(
    r"\b(?:how\s+(?:has|have|was|is|are|did)\s+(?:my|the|this|our|things|everything|i)\b[^.?!]{0,24}\b(?:week|day|month|going|been|life|things)\b"
    r"|how(?:'s|\s+is|\s+has|\s+have)?\s+(?:things|everything|life)\b|how\s+(?:have|am)\s+i\s+(?:been|doing)\b"
    r"|what(?:'s|\s+has|\s+have|\s+is)?\s+(?:been\s+)?(?:going\s+on|happening|new)\b|anything\s+(?:new|going\s+on)\b|catch\s+me\s+up\b"
    r"|(?:this|last)\s+week\b|lately\b|these\s+days\b)", re.IGNORECASE)

_MOOD_RE = re.compile(
    r"\b(?:can'?t\s+(?:switch|turn|shut)\s+(?:my\s+)?(?:brain|mind|head)\s+off|can'?t\s+(?:sleep|stop\s+(?:thinking|worrying))|on\s+edge|my\s+(?:head|mind)\s+is\s+(?:spinning|racing)|"
    r"(?:i'?m|i\s+am|i\s+feel|feeling)\s+(?:really\s+|so\s+|a\s+bit\s+)?(?:anxious|worried|nervous|stressed|overwhelmed|down|sad|low))", re.IGNORECASE)

_CACHE: "dict[str, tuple[float, list, list]]" = {}

#: per-user write lock: the night pass's commit and the erasures (``erase_entity`` / ``erase_words`` / ``delete_user``) never interleave in this process
_LOCKS: "dict[str, asyncio.Lock]" = {}
#: when ``delete_user`` last ran per user (monotonic): a pass that started before it must not write the user's rows back
_DELETED_AT: "dict[str, float]" = {}


def _user_lock(user_id: str) -> "asyncio.Lock":
    lock = _LOCKS.get(user_id)
    if lock is None:
        if len(_LOCKS) > 512:
            _LOCKS.clear()
        lock = _LOCKS[user_id] = asyncio.Lock()
    return lock


def invalidate(user_id: Optional[str] = None) -> None:
    if user_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(user_id, None)


def _reset_state() -> None:
    _CACHE.clear()


async def _shielded(user_id: str, text: str) -> bool:
    """Does the text name an entity the owner asked Zoe to forget (the 300 s tombstone or the durable ledger)? Checked on every serve."""
    try:
        from memory_tombstones import matching_tombstone

        if matching_tombstone(user_id, text):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import memory_forgotten

        return bool(await memory_forgotten.matches(user_id, text))
    except Exception:  # noqa: BLE001
        return False


def _only_significant(threads: "Sequence[dict]", obs: "Sequence[dict]") -> "tuple[list[dict], list[dict]]":
    """What the UNPROMPTED readers (the card, the check-in, the morning item) may serve: threads that are stories (``significant``), and their observations."""
    keep_t = [t for t in threads if significant(t) or fault("echo")]
    ids = {t["id"] for t in keep_t}
    return keep_t, [o for o in obs if o["thread_id"] in ids]


async def snapshot(user_id: str, *, use_cache: bool = False, all_threads: bool = False) -> "tuple[list[dict], list[dict]]":
    """The member's threads and CURRENT observations (a forgotten name re-checked on every read). By default only threads that are stories (``significant``: two days, or heavy, or changed /
    resolved); ``all_threads=True`` also returns a lone NOTABLE remark (weight 2+; a weight-1 fact stays in the store), for the reader that answers a message which NAMES it ('how is my knee' after one line about the knee: the member asked)."""
    now = time.monotonic()
    hit = _CACHE.get(user_id)
    if use_cache and hit and now - hit[0] < _CACHE_TTL_S:
        return (list(hit[1]), list(hit[2])) if all_threads else _only_significant(hit[1], hit[2])
    backend = night_store.get_backend()
    threads = await backend.threads(user_id)
    obs = await backend.observations(user_id, states=("current",))
    keep = []
    for o in obs:
        if not await _shielded(user_id, o["quote"]):
            keep.append(o)
    alive = {o["thread_id"] for o in keep}
    threads = [t for t in threads if t["id"] in alive and (significant(t) or int(t.get("weight_max") or 1) >= 2 or fault("echo"))]      # a minor lone fact (weight 1) stays in the store, never served
    keep = [o for o in keep if o["thread_id"] in {t["id"] for t in threads}]
    _CACHE[user_id] = (now, threads, keep)
    return (list(threads), list(keep)) if all_threads else _only_significant(threads, keep)


def _stale_card(threads: "Sequence[dict]", today: "_dt.date") -> bool:
    """A card older than 36 hours is not served (a stale 'Lately' is worse than none): the newest thread activity is the proxy for 'the last good night'."""
    newest = max((_date(t["last_day"]) for t in threads if _date(t["last_day"])), default=None)
    return newest is None or (today - newest).days > RESOLVED_SHOW_DAYS + 7


def lookup(threads: "Sequence[dict]", obs: "Sequence[dict]", message: str, today: "_dt.date", *, limit: int = SHOW_LINES, per_thread: int = 3,
           include_leave_on_open: bool = False) -> "list[dict]":
    """The observations RELEVANT to this message, best first, each ``{**observation, "thread": thread}``. (1) A message that names a thread's subject
    (a person, a place, a distinctive word) returns that thread's newest quotes - ``leave`` threads included, because the member asked. (2) An open
    check-in ("how has my week been", "what's been going on") returns each live thread's newest quote, by salience, WITHOUT the ``leave`` threads.
    (3) Anything else: nothing. Resolved stories stay for two weeks. Pure."""
    by_thread: "dict[str, list[dict]]" = {}
    for o in obs:
        by_thread.setdefault(o["thread_id"], []).append(o)
    mt = set(_words(message)) - _ANCHOR_STOP
    named: "list[tuple[int, dict]]" = []
    for t in threads:
        a = {x.lstrip("@") for x in str(t.get("anchors") or "").split()} - _ANCHOR_STOP
        title = set(_words(t.get("title") or ""))
        names = {n.lower() for o in by_thread.get(t["id"], []) for n in re.findall(r"[A-Za-z]{3,}", o.get("who") or "")}
        score = 2 * len(mt & (names | title)) + len(mt & a)
        if score and (names & mt or title & mt or len(mt & a) >= 1):
            named.append((score, t))
    pick: "list[dict]"
    mood = False
    if named:
        named.sort(key=lambda x: (x[0], x[1]["last_day"]), reverse=True)
        pick = [t for _s, t in named]
    elif _MOOD_RE.search(message or ""):
        # a worry spoken aloud, not a question: the member's most recent unresolved story that weighed on them (never a leave thread) - one, to be asked about gently
        weighed = [t for t in threads if (significant(t) or fault("echo")) and t["status"] in ("open", "changed") and t.get("last_feeling") in NEGATIVE and not t["leave_reason"]
                   and (today - (_date(t["last_day"]) or today)).days <= MORNING_DAYS + 4]
        weighed.sort(key=lambda t: (t["last_day"], salience(t, today)), reverse=True)
        pick = weighed[:1]
        mood = True
    elif _CHECKIN_RE.search(message or ""):
        live = [t for t in threads if (significant(t) or fault("echo")) and (t["status"] != "resolved" or (today - (_date(t["last_day"]) or today)).days <= RESOLVED_SHOW_DAYS)
                and (include_leave_on_open or not t["leave_reason"])]
        live.sort(key=lambda t: (t["last_day"], salience(t, today)), reverse=True)
        pick = live
    else:
        return []
    out: "list[dict]" = []
    for t in pick:
        rows = sorted(by_thread.get(t["id"], []), key=lambda o: (o["said_at"], o["id"]), reverse=True)[:per_thread if named else 1]
        out += [{**o, "thread": t, "mood": mood} for o in rows]
        if len(out) >= limit:
            break
    return out[:limit]


def _quote_line(o: dict) -> str:
    return f"{fmt_day(o['day'])}: “{o['quote'].replace('[', '(').replace(']', ')')}”"


_MOOD_ASK = ("(They are carrying this. If it fits, check in about it gently, once, in your own words - never read it back to them, and bring up nothing else from their life.)")


def block_text(rows: "Sequence[dict]") -> str:
    if not rows:
        return ""
    body = ("## What I've noticed (the owner's own words, with the day they said them)\n"
            + "\n".join(f"- {_quote_line(o)}" + (" (resolved)" if o['thread']['status'] == 'resolved' else "") for o in rows[:SHOW_LINES]))
    return body + ("\n" + _MOOD_ASK if any(o.get("mood") for o in rows) else "")


async def prompt_block(user_id: str, message: str, *, now: "Optional[_dt.datetime]" = None, served: "Optional[list]" = None) -> str:
    """The recall packet's "What I've noticed" block for this message (<= 3 lines), or ``""``. Flag off = ``""`` with no I/O. Never raises, time-boxed.
    ``leave`` threads appear ONLY when the message names them. ``served``, when given, receives ``(turn_id, said_at, quote)`` of each line shown, so
    the reply's provenance can name the owner's turn it stood on."""
    if not enabled() or not (user_id or "").strip() or not (message or "").strip():
        return ""
    try:
        now = now or _dt.datetime.now(_dt.timezone.utc)
        threads, obs = await asyncio.wait_for(snapshot(user_id, use_cache=True, all_threads=True), timeout=0.6)
        today = now.astimezone(_local_tz()).date()
        rows = lookup(threads, obs, message, today)
        if served is not None:
            for o in rows[:SHOW_LINES]:
                try:
                    said = float(o.get("said_at") or 0.0)
                except (TypeError, ValueError):
                    said = 0.0
                served.append((str(o.get("turn_id") or ""), said, str(o.get("quote") or "")))
        return block_text(rows)
    except Exception as exc:  # noqa: BLE001 - an extra block: the packet is complete without it
        logger.debug("night_mind: prompt block skipped (%s)", type(exc).__name__)
        return ""


async def morning_items(user_id: str, *, now: "Optional[_dt.datetime]" = None) -> "list[dict]":
    """At most ONE brief item: the thread the night decided to ``raise``, as ``{"text", "source_ref", "thread_id"}`` - the owner's dated words as a
    present fact. ``leave`` threads are never here. ``[]`` with the flag off. Never raises."""
    if not enabled() or not (user_id or "").strip():
        return []
    try:
        now = now or _dt.datetime.now(_dt.timezone.utc)
        threads, obs = await asyncio.wait_for(snapshot(user_id, use_cache=True), timeout=0.6)
        today = now.astimezone(_local_tz()).date()
        for t in threads:
            if t["raise_policy"] != "raise" or t["leave_reason"]:
                continue
            last = _date(t["last_day"])
            if last is None or not 0 <= (today - last).days <= MORNING_DAYS:
                continue
            mine = sorted((o for o in obs if o["thread_id"] == t["id"]), key=lambda o: o["said_at"], reverse=True)
            if mine:
                o = mine[0]
                return [{"text": f"On {fmt_day(o['day'])} they said: “{o['quote'].replace('[', '(').replace(']', ')')}”", "source_ref": t["source_ref"], "thread_id": t["id"]}]
    except Exception as exc:  # noqa: BLE001
        logger.debug("night_mind: morning items skipped (%s)", type(exc).__name__)
    return []


async def note_raised(user_id: str, source_refs: "Sequence[str]", *, now: "Optional[_dt.datetime]" = None) -> int:
    """The brief (or a raise) voiced these ``night_threads:<id>`` items: stamp ``last_raised_at`` and move ``next_raise_after`` so tomorrow's pass does not
    raise them again. Never raises."""
    ids = {str(r).split(":", 1)[1] for r in source_refs or () if str(r).startswith("night_threads:")}
    if not ids:
        return 0
    try:
        backend = night_store.get_backend()
        today = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_local_tz()).date()
        n = 0
        for t in await backend.threads(user_id):
            if t["id"] in ids:
                note_raise(t, today)
                t["raise_policy"] = "wait"
                await backend.put_thread(t)
                n += 1
        invalidate(user_id)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.debug("night_mind: note_raised skipped (%s)", type(exc).__name__)
        return 0


# ═══ forgetting and deleting ══════════════════════════════════════════════════════════════════════════════════════════════

async def erase_entity(user_id: str, name: str) -> int:
    """Delete this user's observations (and threads) that name ``name`` - a whole word / phrase, case-blind, separator-blind: the pattern the forget
    sweep uses. A store failure is RAISED (a forget that could not erase must not be confirmed), like ``exact_words.erase_entity``."""
    if not (user_id or "").strip() or not (name or "").strip():
        return 0
    from memory_forgotten import name_pattern

    try:
        async with _user_lock(user_id):
            n = int(await night_store.get_backend().erase_matching(user_id, name_pattern(name)) or 0)
    except Exception as exc:  # noqa: BLE001
        if _no_table(exc):
            return 0
        raise
    invalidate(user_id)
    return n


def _flat(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


async def erase_words(user_id: str, turn_id: str, *texts: str) -> int:
    """Delete this user's observations (and the threads left empty) that keep the words of ONE forgotten turn: those saved from that turn id, and
    those whose quote sits inside - or contains - any of ``texts`` (the displayed quote, the row text). "Forget it" after an explained answer must
    reach what the night pass saved from the same words, which ``erase_entity`` (name-based) does not. A store failure is RAISED."""
    if not (user_id or "").strip():
        return 0
    wanted = [t for t in (_flat(x) for x in texts) if len(t.split()) >= 2]
    tid = str(turn_id or "").strip()
    if not wanted and not tid:
        return 0
    async with _user_lock(user_id):
        try:
            backend = night_store.get_backend()
            hit = [o["quote"] for o in await backend.observations(user_id)
                   if (tid and str(o["turn_id"]) == tid) or any(_flat(o["quote"]) in w or w in _flat(o["quote"]) for w in wanted)]
            hit = [q for q in hit if (q or "").strip()]
            if not hit:
                return 0
            n = int(await backend.erase_matching(user_id, re.compile("|".join(re.escape(q) for q in sorted(set(hit), key=len, reverse=True)))) or 0)
        except Exception as exc:  # noqa: BLE001
            if _no_table(exc):
                return 0
            raise
    invalidate(user_id)
    return n


async def delete_user(user_id: str) -> int:
    """Remove every night-mind row of a user (the audited right-to-be-forgotten path). Raises on a store failure."""
    if not (user_id or "").strip():
        return 0
    _DELETED_AT[user_id] = time.monotonic()
    try:
        async with _user_lock(user_id):
            n = int(await night_store.get_backend().delete_user(user_id) or 0)
    except Exception as exc:  # noqa: BLE001
        if _no_table(exc):
            return 0
        raise
    invalidate(user_id)
    return n


def _no_table(exc: BaseException) -> bool:
    """The 0040 migration has not been applied on this database: there is nothing of the night mind's to erase (it could not have written)."""
    text = f"{type(exc).__name__} {exc}".lower()
    return "undefinedtable" in text or "does not exist" in text or "no such table" in text
