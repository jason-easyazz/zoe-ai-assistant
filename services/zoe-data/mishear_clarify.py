"""Ask "Sorry, what was that?" instead of answering a short line the speech-to-text probably got wrong - flag ``ZOE_MISHEAR_CLARIFY=off|shadow|on`` (default ``off``).

THE FAILURE (owner, 2026-10-14): "Zoe keeps engaging me like I have an emotional problem, even when I just ask her to engage the lights." A short
spoken device command was misheard by Moonshine as a short feeling-shaped line, the STT call was unusually slow (1.47 s for a clip of that length), the line
routed to chat, and the brain answered it as a disclosure ("It sounds like you're carrying a heavy load ..."). The command was repeated 16 s later and worked.
Nothing downstream can know the words were wrong; the only evidence is on the STT side, and it is thin.

WHAT IT DOES. After STT and before ANYTHING else (no transcript broadcast, no memory write, no brain), one decision per turn:

* ``clarify`` - speak one light, varied "Sorry, what was that?" line and leave the panel's normal follow-up window open (the reply is a question, so
  the daemon opens it and sends ``prev_reply_question`` with the repeat). The garbled line is never stored: the turn leaves a log line and nothing else.
* ``answer`` - go on exactly as today. ALWAYS the outcome when in doubt; the reason says why (``disclosure``, ``command``, ``repeat``, ...).

A turn is clarified only when EVERY one of these holds (each is a veto on its own; erring toward answering is the safer failure - ignoring someone's
feelings is worse than answering a mishearing):

1. the flag is ``on`` and the turn is a real, unspeculative, un-merged one;
2. it is SHORT (``MAX_WORDS``, 5) and the language has a lexicon entry (``lexicons_data/<lang>.json`` ``mishear_clarify``; a language with none is inert);
3. the STT signal looks unreliable (``stt_signals``, below);
4. it is feeling-shaped: a first-person statement or a "feel" lead, and not a question;
5. it is NOT a clear disclosure: no explicit cue from the distress hand-off, the restraint classes (affect / health / grief / family conflict), the risk-cue
   tier, or this module's own extra cues (``mishear_clarify.cues`` in the lexicon). "I'm really stressed about work" carries "stressed": always answered;
6. no deterministic owner wants it (a conversation opener / ender, a keyword intent, a Skybridge card, a goodbye, a "what's up" pull, a spoken mute);
7. the router says CHAT (an unavailable router or any tool domain => answer: a tool turn is never second-guessed);
8. Zoe did not just ask a question (``prev_reply_question``: a short "not great" after "how was your day?" is an answer, not a mishearing);
9. it is not the repeat of a turn already clarified on this panel: ONE clarification in a row, then the next turn is answered whatever it is.

WHICH STT SIGNALS EXIST (read from the code, 2026-10-14). Moonshine v2 via ``moonshine-voice`` 0.1.3 is called with ``transcribe_without_streaming``; the
transcript lines carry text, ``last_transcription_latency_ms`` and ``words`` - but ``words`` (per-word ``confidence``) is filled ONLY with the optional
attention decoder (``word_timestamps``), which this deployment does not load, and there is no no-speech probability anywhere. So there is NO engine
confidence to read. What the pipeline has per turn is: the STT wall time (``t_stt``), the clip length (the WAV header), and the transcript. From these:

* ``slow_stt`` - ``t_stt >= SLOW_STT_MIN_S`` AND ``t_stt / clip >= SLOW_STT_RATIO`` AND (when at least ``BASELINE_MIN`` earlier turns are known) the ratio is
  ``RELATIVE_FACTOR`` times the recent median. Measured on 1,497 short turns (voice_stt.jsonl, replays + live): ratio p50 0.19, p90 0.35, p99 0.52, so
  0.6 sits above p99 and fires on ~0.4% of short turns; the relative clause stops a LOADED box (every turn slow) from clarifying everything.
* ``sparse`` - a clip of ``SPARSE_MIN_CLIP_S`` or more yielding at most ``SPARSE_MAX_WPS`` words per second AND already somewhat slow (ratio >= ``SPARSE_RATIO``):
  a long recording that produced almost no words. Words-per-second alone (p5 = 0.49) and a missing terminal full stop (24% of short live lines) were
  measured and are NOT signals by themselves.

MODES. ``off`` (default): ``evaluate`` returns at once, nothing runs, the wire bytes are today's. ``shadow``: everything runs and one metric line says what
``on`` would have done; the turn is answered as today. ``on``: the clarification. One line per short turn, never any transcript text::

    MISHEAR_CLARIFY mode=on decision=clarify reason=unreliable words=4 clip=1.60 stt=1.47 ratio=0.92 base=0.20 signals=slow_stt lang=en routed=chat window=-

Stdlib only apart from lazy imports of the lexicon-driven modules; ``evaluate`` never raises into the turn (the caller also guards it).
"""
from __future__ import annotations

import logging
import re
import struct
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Optional

from typed_env import env_str

logger = logging.getLogger(__name__)

FLAG = "ZOE_MISHEAR_CLARIFY"
LEXICON_KEY = "mishear_clarify"

#: a line longer than this is never second-guessed: enough words carry their own context
MAX_WORDS = 5
#: ``slow_stt``: absolute floor, ratio to the clip, and how far above the recent median the ratio must be
SLOW_STT_MIN_S = 1.0
SLOW_STT_RATIO = 0.6
RELATIVE_FACTOR = 2.5
#: earlier turns needed before the recent median counts (fewer => the absolute rule alone)
BASELINE_MIN = 3
BASELINE_WINDOW = 8
#: ``sparse``: a long clip with almost no words, and already somewhat slow
SPARSE_MIN_CLIP_S = 2.0
SPARSE_MAX_WPS = 0.5
SPARSE_RATIO = 0.4
#: after a clarification the very next turn on that panel is answered, whatever it is; the memory expires after this
REPEAT_TTL_S = 90.0

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


def mode() -> str:
    """``off`` (default; an unrecognised value is off - a typo never starts clarifying) | ``shadow`` | ``on``. Read per call."""
    raw = env_str("ZOE_MISHEAR_CLARIFY", "off").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "on"
    if raw == "shadow":
        return "shadow"
    return "off"


# -- the decision ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    action: str = "off"            # off | answer | clarify
    reason: str = ""
    signals: tuple = ()
    words: int = 0
    lang: str = ""
    routed: Optional[str] = None
    router: Optional[dict] = None  # the router verdict computed on the way, handed to the turn so it is not computed twice
    reply: str = ""                # the clarification line (action == clarify)
    shadow: bool = False           # shadow mode: this WOULD have clarified, the turn is answered as today


OFF = Decision()


# -- STT evidence ---------------------------------------------------------------------------------------------------------------
def wav_seconds(raw: bytes) -> Optional[float]:
    """Clip length from a RIFF/WAVE header, or None. Any sample format; a streamed header (data size 0 / 0xFFFFFFFF) reads to the end of the buffer."""
    try:
        if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            return None
        pos, byte_rate = 12, 0
        while pos + 8 <= len(raw):
            cid, size = raw[pos:pos + 4], struct.unpack_from("<I", raw, pos + 4)[0]
            if cid == b"fmt " and pos + 8 + 16 <= len(raw):
                ch, rate, _brate, _align, bits = struct.unpack_from("<HIIHH", raw, pos + 10)
                byte_rate = rate * ch * (bits // 8)
            elif cid == b"data":
                room = len(raw) - pos - 8
                n = room if size in (0, 0xFFFFFFFF) or size > room else size
                return round(n / byte_rate, 3) if byte_rate > 0 else None
            pos += 8 + size + (size & 1)
    except Exception:  # noqa: BLE001 - evidence only; a header that does not parse is no evidence
        return None
    return None


_RATIOS: "deque[float]" = deque(maxlen=BASELINE_WINDOW)
_PENDING: dict[str, float] = {}   # panel -> monotonic time of the clarification it just got
_COUNTS: dict[tuple, int] = {}
_ROTATE = [0]


def _baseline() -> Optional[float]:
    if len(_RATIOS) < BASELINE_MIN:
        return None
    xs = sorted(_RATIOS)
    return xs[len(xs) // 2]


def stt_signals(words: int, clip_s: Optional[float], stt_s: Optional[float], base: Optional[float]) -> tuple:
    """Which unreliability signals this turn's STT evidence raises (module docstring). Pure."""
    if not clip_s or not stt_s or clip_s <= 0 or stt_s <= 0:
        return ()
    ratio = stt_s / clip_s
    out = []
    if stt_s >= SLOW_STT_MIN_S and ratio >= SLOW_STT_RATIO and (base is None or ratio >= RELATIVE_FACTOR * base):
        out.append("slow_stt")
    if clip_s >= SPARSE_MIN_CLIP_S and words / clip_s <= SPARSE_MAX_WPS and ratio >= SPARSE_RATIO:
        out.append("sparse")
    return tuple(out)


# -- lexicon-driven shape checks ------------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return (text or "").lower().replace("’", "'")


def _rx(items: Any) -> Optional["re.Pattern[str]"]:
    parts = [str(i) for i in (items or []) if str(i).strip()]
    if not parts:
        return None
    return re.compile(r"(?<![^\W_])(?:" + "|".join(parts) + r")(?![^\W_])", re.IGNORECASE)


@dataclass(frozen=True)
class _Pack:
    lang: str
    phrases: tuple
    cues: Optional["re.Pattern[str]"]
    first_person: frozenset
    feel: Optional["re.Pattern[str]"]


@lru_cache(maxsize=None)
def _pack(lang: str) -> Optional[_Pack]:
    """The language's data, or None when it has no ``mishear_clarify`` entry (the guard is then inert; it is never guessed from English)."""
    import lexicons

    lex = lexicons.load(lang) or {}
    entry = lex.get(LEXICON_KEY)
    phrases = tuple(str(p).strip() for p in ((entry or {}).get("phrases") or []) if str(p).strip())
    if not isinstance(entry, dict) or not phrases:
        return None
    return _Pack(
        lang=lang,
        phrases=phrases,
        cues=_rx(entry.get("cues")),
        first_person=frozenset(_norm(w) for w in (lex.get("first_person") or [])),
        feel=_rx((lex.get("restraint") or {}).get("feel_lead")),
    )


def _tokens(text: str) -> list:
    return _WORD_RE.findall(_norm(text))


def _choose_lang(text: str) -> str:
    """The language the line is in. A short line carries too few function words for ``lexicons.detect`` (it answers ``en`` with no evidence), so with
    NO evidence the language whose own "feel" lead the line carries ("me siento ...") wins; otherwise the detector's answer stands."""
    import lexicons

    lang, evidence = lexicons.detect_scored(text)
    if evidence == 0:
        low = _norm(text)
        for code in lexicons.LANGS:
            p = _pack(code)
            if p is not None and p.feel is not None and p.feel.search(low):
                return code
    return lang or "en"


def is_question(text: str, lang: str = "en") -> bool:
    s = (text or "").strip()
    if s.endswith(("?", "？")) or s.startswith("¿"):
        return True
    import lexicons

    first = (_tokens(s) or [""])[0]
    return first in set(lexicons.words(lang, "question_aux")) | set(lexicons.words(lang, "question_words"))


def feeling_shaped(text: str, pack: _Pack) -> bool:
    """A first-person statement or a "feel" lead, and not a question: the shape the 4B answers as a disclosure."""
    if is_question(text, pack.lang):
        return False
    toks = _tokens(text)
    if any(t in pack.first_person for t in toks):
        return True
    return bool(pack.feel and pack.feel.search(_norm(text)))


def disclosure_reason(text: str, pack: _Pack) -> str:
    """Why this line must reach the brain as a possible disclosure ('' when it carries no explicit cue). Every source is an EXISTING lexicon tier:
    the distress hand-off (any tier), the restraint classes affect / health / grief / family conflict, the risk-cue tier, plus this module's own cues."""
    low = _norm(text)
    try:
        import distress_handoff

        v = distress_handoff.detect(text)
        if v.tier != "none":
            return f"distress:{v.tier}"
    except Exception:  # noqa: BLE001 - a failing tier must never turn into "clarify"
        return "distress:error"
    try:
        import restraint_lex

        for p in restraint_lex.packs_for(text):
            for cls, rx in (("affect", p.affect), ("health", p.health), ("grief", p.grief), ("family_conflict", p.family_conflict)):
                if rx is not None and rx.search(low):
                    return f"restraint:{cls}"
    except Exception:  # noqa: BLE001
        return "restraint:error"
    try:
        import risk_cues

        if risk_cues.detect(text):
            return "risk_cue"
    except Exception:  # noqa: BLE001
        return "risk_cue:error"
    import lexicons

    for code in lexicons.LANGS:
        other = _pack(code)
        if other is not None and other.cues is not None and other.cues.search(low):
            return "cue"
    return ""


def deterministic_owner(text: str, *, in_conversation: bool = False) -> str:
    """The deterministic tier that already owns this line, or ''. Cheap regex tiers only; run on the handful of turns that got this far."""
    try:
        import conversation_opener as co

        if co.conversation_opener_enabled():
            if co.maybe_conversation_opener(text):
                return "opener"
            if in_conversation and co.is_conversation_ender(text):
                return "ender"
    except Exception:  # noqa: BLE001
        return "opener:error"
    try:
        from intent_router import detect_intent

        intent = detect_intent(text, log_miss=False)
        if intent is not None:
            return f"intent:{getattr(intent, 'name', '') or 'unknown'}"
        from skybridge_service import classify_skybridge_intent

        if classify_skybridge_intent(text, None) is not None:
            return "skybridge"
        import clean_goodbye

        if clean_goodbye.classify(text):
            return "goodbye"
        import restraint

        if restraint.is_pull(text) or restraint.parse_utterance(text) is not None:
            return "restraint_phrase"
    except Exception:  # noqa: BLE001 - when a tier cannot be asked, assume it owns the line
        return "owner:error"
    return ""


# -- the metric line ------------------------------------------------------------------------------------------------------------
def _log(m: str, d: Decision, *, clip: Optional[float], stt: Optional[float], base: Optional[float], window: str) -> None:
    key = (m, d.action, d.reason, d.shadow)
    _COUNTS[key] = _COUNTS.get(key, 0) + 1
    ratio = (stt / clip) if clip and stt and clip > 0 else 0.0
    logger.info(
        "MISHEAR_CLARIFY mode=%s decision=%s reason=%s words=%d clip=%.2f stt=%.2f ratio=%.2f base=%s signals=%s lang=%s routed=%s window=%s",
        m, "would_clarify" if d.shadow else d.action, d.reason or "-", d.words, clip or 0.0, stt or 0.0, ratio,
        f"{base:.2f}" if base is not None else "-", ",".join(d.signals) or "-", d.lang or "-", d.routed or "-", window or "-",
    )


def counts() -> dict:
    return dict(_COUNTS)


def reset_state() -> None:
    _RATIOS.clear()
    _PENDING.clear()
    _COUNTS.clear()
    _ROTATE[0] = 0
    _pack.cache_clear()


def forget(panel_id: str) -> None:
    """Drop the "just clarified" memory of a panel (the clarification could not be spoken, so the next turn is judged afresh)."""
    _PENDING.pop(panel_id or "-", None)


def _phrase(pack: _Pack) -> str:
    i = _ROTATE[0]
    _ROTATE[0] += 1
    return pack.phrases[i % len(pack.phrases)]


# -- the entry point ------------------------------------------------------------------------------------------------------------
def evaluate(
    transcript: str,
    *,
    panel_id: str = "",
    clip_s: Optional[float] = None,
    stt_s: Optional[float] = None,
    window: str = "",
    prev_reply_question: bool = False,
    in_conversation: bool = False,
    speculative: bool = False,
    continued: bool = False,
    route_fn: Optional[Callable[[str], Optional[dict]]] = None,
    now: Optional[float] = None,
) -> Decision:
    """The one decision for this turn. Cheapest checks first: with the flag off this is one env read; on, a long or ordinary turn costs a dict lookup,
    a regex word count and a deque update. The lexicon tiers, the router and the keyword intents run only for the rare short + unreliable turn."""
    m = mode()
    if m == "off":
        return OFF
    t = now if now is not None else time.monotonic()
    key = panel_id or "-"
    text = (transcript or "").strip()
    n = len(_WORD_RE.findall(text))
    base = _baseline()
    ratio = (stt_s / clip_s) if clip_s and stt_s and clip_s > 0 else None

    def done(d: Decision, *, log: bool) -> Decision:
        if ratio is not None:
            _RATIOS.append(ratio)
        if log:
            _log(m, d, clip=clip_s, stt=stt_s, base=base, window=window)
        return d

    # 9. one clarification in a row: the turn after one is answered, whatever it is; the memory then clears
    asked = _PENDING.pop(key, None)
    if asked is not None and t - asked <= REPEAT_TTL_S:
        return done(Decision("answer", "repeat", words=n), log=True)
    if speculative or continued or not text or n == 0 or n > MAX_WORDS:
        return done(Decision("answer", "not_short", words=n), log=False)

    signals = stt_signals(n, clip_s, stt_s, base)
    if not signals:
        return done(Decision("answer", "reliable", words=n), log=True)

    import lexicons

    lang = _choose_lang(text)
    pack = _pack(lang)
    if pack is None:
        return done(Decision("answer", "no_lexicon", signals, n, lang), log=True)
    if prev_reply_question:
        return done(Decision("answer", "answers_question", signals, n, lang), log=True)
    why = disclosure_reason(text, pack)
    if why:
        return done(Decision("answer", f"disclosure:{why}", signals, n, lang), log=True)
    if not feeling_shaped(text, pack):
        return done(Decision("answer", "not_feeling_shaped", signals, n, lang), log=True)
    owner = deterministic_owner(text, in_conversation=in_conversation)
    if owner:
        return done(Decision("answer", f"command:{owner}", signals, n, lang), log=True)
    router = None
    try:
        router = route_fn(text) if route_fn is not None else None
    except Exception:  # noqa: BLE001
        router = None
    routed = (router or {}).get("routed") if isinstance(router, dict) else None
    if routed != "chat":
        return done(Decision("answer", "command:router" if routed else "router_unavailable", signals, n, lang, routed, router), log=True)

    if m == "shadow":
        return done(Decision("answer", "unreliable", signals, n, lang, routed, router, shadow=True), log=True)
    _PENDING[key] = t
    if len(_PENDING) > 64:  # bounded: a household has a handful of panels
        for k in [k for k, v in _PENDING.items() if t - v > REPEAT_TTL_S]:
            _PENDING.pop(k, None)
    return done(Decision("clarify", "unreliable", signals, n, lang, routed, router, reply=_phrase(pack)), log=True)
