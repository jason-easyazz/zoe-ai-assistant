"""The reasoning scheduler's ONE choke point: no daytime 4B call outside a person's turn unless it is a declared exception.

Policy (owner decision 2026-11-03, ``docs/research/samantha-initiative-2026-11-03.md`` roadmap 5): between 07:00 and 22:00
household time the 4B brain's one llama slot belongs to a person's turn. Anything else that wants to generate on it is either
a DECLARED exception or an *idle 4B call* - the metric this module counts (target 0).

Where it bites. Every zoe-data caller reaches the brain with ``httpx`` (``/v1/chat/completions`` or ``/completion`` on the
brain port), so :func:`install` wraps ``httpx.AsyncClient.send`` once, at start-up: one choke point, which also catches a caller
written next month. The caller is named by (1) the label a :func:`declare` context set (``brain_slot_guard.run_background``
does this for the post-turn passes), else (2) the calling ``module.function`` found on the stack. The label is classified:

  ``turn``       inside a person's request (the legacy agent loop, the NLU slot extractor, the intent classifier, a card
                 composed for the reply just given, anything inside ``declare(..., TURN)``). Always allowed.
  ``post_turn``  a memory pass derived from a person's turn that just finished (``DECLARED`` below). Allowed only while a
                 household turn ended within ``ZOE_BRAIN_SCHEDULER_POST_TURN_S`` (default 900 s); older than that it is not
                 derived from a live turn any more, so it is *idle*.
  ``exception``  declared by hand: the wake prewarm and the idle-time prefix keeper (they load the household prefix so the
                 NEXT person's turn is a cache hit; see :func:`note`). Extra labels via ``ZOE_BRAIN_SCHEDULER_ALLOW``.
  ``request``    one person asked for it explicitly over HTTP (contact backfill).
  ``idle``       everything else: nightly digest passes, portrait synthesis, proactive composing, idle consolidation,
                 harnesses. Outside the day window it is allowed ("night"); inside it, it is the violation.

Modes (``ZOE_BRAIN_SCHEDULER``): ``off`` (a no-op), ``log`` (DEFAULT: decide, count and log, never block - ``would_defer``),
``enforce`` (a daytime idle call raises :class:`BrainSchedulerDeferred` before it touches the slot; every caller already
treats a failed call as "no result" and retries on its own schedule, which for the night passes is the night).

One log line per decision, no content: ``BRAIN_SCHED caller=<label> class=<cls> decision=<d> reason=<r> mode=<m> idle=<n>``
(``decision`` is ``allowed``, ``would_defer`` or ``deferred``). ``idle_4b_calls`` in :data:`STATS` is the same count;
the log line is the durable record (zoe-data restarts ~17x/day).

Declared-exception evidence, 2026-11-05, from ``~/.zoe-logs/zoe-data.app.log*`` (``BRAIN_SLOT_GUARD`` lines, 3 rotations,
2026-10-10 .. 11-05): ``person_extractor_llm`` waited 52-240 s on the gate on average per rotation, 7 of 28 gated calls hit the
240 s ``released_max`` ceiling. It stays ``post_turn`` because a refusal LOSES the fact (``process_text_llm`` returns 0 and nothing re-queues it), while
the slot guard already keeps it from touching a hot conversation. Strictness is a knob (the window), not a code change.
"""
from __future__ import annotations

import contextvars
import logging
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

from typed_env import env_float, env_str

logger = logging.getLogger(__name__)

ENV_MODE = "ZOE_BRAIN_SCHEDULER"                  # off | log (default) | enforce
ENV_DAY = "ZOE_BRAIN_SCHEDULER_DAY"               # "HH:MM-HH:MM" household time, default 07:00-22:00
ENV_ALLOW = "ZOE_BRAIN_SCHEDULER_ALLOW"           # extra comma-separated exception labels
ENV_POST_TURN_S = "ZOE_BRAIN_SCHEDULER_POST_TURN_S"
DEFAULT_DAY = "07:00-22:00"
DEFAULT_POST_TURN_S = 900.0

TURN, POST_TURN, EXCEPTION, REQUEST, IDLE = "turn", "post_turn", "exception", "request", "idle"

# label -> (class, why). Labels are what ``brain_slot_guard.run_background`` is given.
DECLARED: dict[str, tuple[str, str]] = {
    "person_extractor_llm": (POST_TURN, "post-turn people extractor; a refusal loses the fact"),
    "turn_digest": (POST_TURN, "post-turn digest of the turn just spoken"),
    "latent_intent_detector": (POST_TURN, "post-turn can't-do / latent-intent pass"),
    "structural_reader": (POST_TURN, "post-turn structural read of the turn"),
    "structural_verifier": (POST_TURN, "post-turn verifier of that read"),
    "quote_retire_judge": (POST_TURN, "post-turn retirement judge for the turn"),
    "memory_claims_pass": (POST_TURN, "idle claim read of a correction turn just spoken (ZOE_MEMORY_CLAIMS, default off)"),
    "wake_prewarm": (EXCEPTION, "loads the household prefix when a person wakes the panel"),
    "prefix_keeper": (EXCEPTION, "re-loads the household prefix a helper batch displaced, so the next wake is warm"),
    "contact_backfill._llm_extract_people": (REQUEST, "explicit person-triggered contact backfill"),
    "conversation_notes": (REQUEST, "the nightly conversation-note pass and the owner-run guest backfill (never a daytime idle call)"),
}
# module/function prefixes that ARE a person's turn (the request is in flight when they run).
TURN_PREFIXES = ("zoe_agent.", "nlu_extractor.", "intent_classifier_llm.", "ui_compose.", "turn:")

_GENERATION_PATHS = ("/v1/chat/completions", "/chat/completions", "/completion", "/v1/completions", "/completions")
_SKIP_FRAME_PREFIXES = ("httpx", "httpcore", "anyio", "asyncio", "contextlib", "brain_scheduler")

STATS: dict = {"idle_4b_calls": 0, "turn": 0, "post_turn": 0, "exception": 0, "request": 0, "night_idle": 0,
               "deferred": 0, "would_defer": 0, "by_caller": {}}
_stats_lock = threading.Lock()
_ctx: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("brain_scheduler_label", default=None)
_orig_send = None


class BrainSchedulerDeferred(Exception):
    """``enforce`` mode refused a daytime idle 4B call. An ``Exception``: every caller already treats a failed call as 'no result'."""


@dataclass(frozen=True)
class Decision:
    label: str
    cls: str
    decision: str   # allowed | would_defer | deferred
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision != "deferred"


def mode() -> str:
    raw = (env_str(ENV_MODE, "log") or "log").strip().lower()
    if raw in ("0", "off", "false", "no", "disabled"):
        return "off"
    return "enforce" if raw == "enforce" else "log"


def _parse_day(raw: str) -> tuple[int, int]:
    """``HH:MM-HH:MM`` as (start, end) minutes. ``start > end`` is an overnight window; equal bounds are invalid."""
    try:
        a, b = (raw or "").split("-")
        (ah, am), (bh, bm) = (int(x) for x in a.split(":")), (int(x) for x in b.split(":"))
        lo, hi = ah * 60 + am, bh * 60 + bm
        if 0 <= lo <= 24 * 60 and 0 <= hi <= 24 * 60 and lo != hi:
            return lo, hi
    except (ValueError, TypeError):
        pass
    return _parse_day(DEFAULT_DAY)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(env_str("ZOE_TIMEZONE", "Australia/Perth") or "Australia/Perth")
    except Exception:  # noqa: BLE001 - a bad zone name must not stop the brain
        return ZoneInfo("Australia/Perth")


def is_day(now: Optional[datetime] = None) -> bool:
    """True inside the day window (household time): the hours the slot belongs to a person's turn."""
    local = (now or datetime.now(_tz())).astimezone(_tz())
    lo, hi = _parse_day(env_str(ENV_DAY, DEFAULT_DAY) or DEFAULT_DAY)
    t = local.hour * 60 + local.minute
    return lo <= t < hi if lo < hi else (t >= lo or t < hi)


def _post_turn_window_s() -> float:
    v = env_float(ENV_POST_TURN_S, DEFAULT_POST_TURN_S)
    return v if v >= 0 else DEFAULT_POST_TURN_S


def _extra_allowed() -> set:
    return {x.strip() for x in (env_str(ENV_ALLOW, "") or "").split(",") if x.strip()}


def _turn_age_s() -> Optional[float]:
    import brain_slot_guard   # lazy: brain_slot_guard may import this module's declare()

    return brain_slot_guard.last_turn_age_s()


def classify(label: str) -> tuple[str, str]:
    """(class, reason) for a label, before the post-turn freshness rule."""
    if label in DECLARED:
        return DECLARED[label]
    if label in _extra_allowed():
        return EXCEPTION, "listed in " + ENV_ALLOW
    if label.startswith(TURN_PREFIXES):
        return TURN, "inside a person's request"
    return IDLE, "not a turn and not declared"


def decide(label: str, *, now: Optional[datetime] = None) -> Decision:
    """Pure policy: the decision for ``label`` right now. Counts and logs nothing (see :func:`admit`)."""
    cls, reason = classify(label)
    if cls == POST_TURN:
        age = _turn_age_s()
        if age is None or age > _post_turn_window_s():
            cls, reason = IDLE, "post-turn pass but no household turn within %ds" % int(_post_turn_window_s())
    if cls != IDLE:
        return Decision(label, cls, "allowed", reason)
    if not is_day(now):
        return Decision(label, cls, "allowed", "night")
    return Decision(label, cls, "deferred" if mode() == "enforce" else "would_defer", reason)


def _record(d: Decision) -> None:
    with _stats_lock:
        if d.cls == IDLE and d.reason == "night":
            STATS["night_idle"] += 1
        elif d.cls == IDLE:
            STATS["idle_4b_calls"] += 1
            STATS["deferred" if d.decision == "deferred" else "would_defer"] += 1
            STATS["by_caller"][d.label] = STATS["by_caller"].get(d.label, 0) + 1
        else:
            STATS[d.cls] += 1
    line = ("BRAIN_SCHED caller=%s class=%s decision=%s reason=%s mode=%s idle=%d"
            % (d.label, d.cls, d.decision, d.reason.replace(" ", "_"), mode(), STATS["idle_4b_calls"]))
    (logger.debug if d.cls == TURN else logger.info)(line)


def admit(label: str, *, now: Optional[datetime] = None) -> Decision:
    """Decide, count and log one 4B call. Raises :class:`BrainSchedulerDeferred` only in ``enforce`` mode."""
    if mode() == "off":
        return Decision(label, "-", "allowed", "scheduler off")
    d = decide(label, now=now)
    _record(d)
    if not d.allowed:
        raise BrainSchedulerDeferred(label)
    return d


def note(label: str) -> None:
    """Record a declared-exception 4B use that does not travel through ``httpx`` here (the Flue-routed prewarms)."""
    if mode() != "off":
        _record(decide(label))


@contextmanager
def declare(label: str) -> Iterator[None]:
    """Name every brain call made inside this block (and tasks created in it). ``TURN_PREFIXES`` / ``DECLARED`` classify it."""
    tok = _ctx.set(label)
    try:
        yield
    finally:
        _ctx.reset(tok)


def _caller_label() -> str:
    lab = _ctx.get()
    if lab:
        return lab
    f = sys._getframe(1)
    while f is not None:
        mod = f.f_globals.get("__name__", "?")
        if not mod.startswith(_SKIP_FRAME_PREFIXES):
            return "%s.%s" % (mod, f.f_code.co_name)
        f = f.f_back
    return "unknown"


_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _effective_port(url) -> int:
    return url.port or (443 if url.scheme == "https" else 80)


def _brain_target() -> tuple[str, int]:
    from urllib.parse import urlsplit

    from gemma_endpoint import gemma_base

    u = urlsplit(gemma_base())
    return (u.hostname or "127.0.0.1"), (u.port or (443 if u.scheme == "https" else 80))


def is_brain_generation(request) -> bool:
    """True for a generation request to the brain's llama-server (not /slots, /tokenize or /health).

    The host must be the configured one; a loopback brain also matches any loopback spelling (127.0.0.1 / localhost / ::1),
    a remote brain matches only itself."""
    try:
        host, port = _brain_target()
        u = request.url
        if _effective_port(u) != port or u.path.rstrip("/") not in _GENERATION_PATHS:
            return False
        h = (u.host or "").strip("[]").lower()
        return h == host.lower() or (host.lower() in _LOOPBACK and h in _LOOPBACK)
    except Exception:  # noqa: BLE001 - classification must never break a request
        return False


def install() -> bool:
    """Wrap ``httpx.AsyncClient.send`` once. Idempotent. The wrapper is a pass-through when the mode is ``off``."""
    global _orig_send
    if _orig_send is not None:
        return False
    import httpx

    _orig_send = httpx.AsyncClient.send

    async def send(self, request, *args, **kwargs):
        try:
            if mode() != "off" and is_brain_generation(request):
                admit(_caller_label())
        except BrainSchedulerDeferred:
            raise
        except Exception:  # noqa: BLE001 - fail OPEN: a scheduler fault must never break (or change) a brain request
            logger.debug("brain_scheduler: classification failed; request passed through", exc_info=True)
        return await _orig_send(self, request, *args, **kwargs)

    httpx.AsyncClient.send = send
    return True


def uninstall() -> None:
    """Test hook."""
    global _orig_send
    if _orig_send is not None:
        import httpx

        httpx.AsyncClient.send = _orig_send
        _orig_send = None


def reset() -> None:
    """Test hook."""
    with _stats_lock:
        for k, v in list(STATS.items()):
            STATS[k] = {} if isinstance(v, dict) else 0
