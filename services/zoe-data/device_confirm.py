"""Confirm-then-act for a garbled short light command at the panel (``ZOE_DEVICE_CONFIRM_GUARD``, default ON).

Live 2026-11-06/07 (the owner, quiet night commands to the bedroom panel): "turn on / off the light", said softly, reached
Zoe as a sound-alike - the verb and its particle fused into one word ("shown"), the noun heard as "lot" / "lines" / "one"
/ "1". No deterministic matcher claimed the words, the router guessed lists / memory / music / chat, and the brain answered
a request nobody made ("I'm Zoe.", a feelings reply, or a promise to switch the light with no tool call). Every sitting
opened that way, and every first attempt failed.

``decide`` asks one narrow question about a transcript nothing claims: is it a SHORT command whose last content word is
a light-like noun after a determiner ("the lot", "a lot", "the one"), with a command-like verb before it, and nothing
after it but an on/off particle? Then Zoe asks ONE short question ("Light on?" / "Turn the light off?") and a plain yes
runs the real Skybridge command (``routers/voice_tts.py`` reuses the STT-snap pending confirmation: the "yes" turn's
words BECOME the command, so every gate - PIN, guest policy, home control, the router veto - judges it like any other,
and the spoken receipt is the resolver's own "Turned Bedroom Light on."). A "no" cancels; anything else is a new request.

Never: on a turn a matcher claims, a question, a turn with "you" in it, a turn the router confidently filed under
smart_home (the brain's home tool owns it), a turn that is not the panel's voice lane, a turn whose caller may not drive
the house, or a turn in the middle of a conversation about something else (``mid_conversation``). The words are DATA
(``lexicons_data/en.json`` "device_confirm"); no word lives here. Pure apart from the per-panel last-turn clock; never
raises into a turn. Contract: docs/knowledge/voice-pipeline.md -> *Device confirm guard*.
"""
from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Optional

import command_shapes
import lexicons

logger = logging.getLogger(__name__)

#: a turn this soon after the panel's previous (non-device) turn is the middle of a conversation
MID_CONVERSATION_S = 90.0
#: a confident smart_home verdict (not gated, at least this sure) is the brain's home tool's turn, not a guess to confirm
HOME_CONFIDENT = 0.8
_MAX_PANELS = 128
_TOKEN_RE = re.compile(r"[a-z0-9']+")

#: panel -> (monotonic time of its last turn that reached the guard, was that turn about a device?)
_LAST_TURN: "OrderedDict[str, tuple[float, bool]]" = OrderedDict()


def enabled() -> bool:
    try:
        from typed_env import flag

        return bool(flag("ZOE_DEVICE_CONFIRM_GUARD"))
    except Exception:  # noqa: BLE001 - an unreadable flag keeps the declared default (on)
        return True


def at_the_panel(caller: Optional[dict]) -> bool:
    """The guard is the PANEL's: a wall-panel voice turn (the daemon / a device token), never a member's own session (desktop,
    phone push-to-talk) - the same split as ``voice_command``'s ``_panel_caller``."""
    return (caller or {}).get("source") != "session"


@dataclass(frozen=True)
class Ask:
    heard: str
    polarity: str        # "on" | "off"
    question: str        # what Zoe says ("Light on?")
    command: str         # what a yes runs ("turn on the light")

    @property
    def intent(self) -> str:
        return f"device_confirm/turn_{self.polarity}"


def _data() -> dict:
    return lexicons.load("en").get("device_confirm") or {}


def _words(sec: dict, key: str) -> frozenset:
    return frozenset(str(w).strip().lower() for w in (sec.get(key) or []) if str(w).strip())


def shape(text: str) -> Optional[str]:
    """``"on"`` / ``"off"`` when ``text`` has the shape of a garbled short light command, else None. Pure (lexicon data)."""
    sec = _data()
    if not sec or "?" in (text or ""):
        return None
    toks = _TOKEN_RE.findall(command_shapes.command_core(text))
    if not (2 <= len(toks) <= int(sec.get("max_words") or 6)):
        return None
    if toks[0] in _words(sec, "question_openers") or _words(sec, "blockers").intersection(toks):
        return None
    nouns, verbs, dets = _words(sec, "nouns"), _words(sec, "verbs"), _words(sec, "determiners")
    on_w, off_w, tail_ok = _words(sec, "on_words"), _words(sec, "off_words"), _words(sec, "tail_ok")
    n = max((i for i, t in enumerate(toks) if t in nouns), default=-1)
    # the light-like noun is the LAST content word, after a determiner ("the lot", "a lot"): "a lot of patience", "this one"
    # and "the one with the cheese" are not a device
    if n < 1 or toks[n - 1] not in dets or not all(t in tail_ok for t in toks[n + 1:]):
        return None
    if not any(t in verbs for t in toks[:n - 1]):
        return None          # "I've had a lot on": a light-like word and a particle, but nothing that commands
    said_on = bool(on_w.intersection(toks))
    said_off = bool(off_w.intersection(toks))
    if said_on and said_off:
        return None          # both particles: no single reading to offer
    if said_off:
        return "off"
    return "on"              # "on" said, a fused on-verb ("shown"), or no particle at all: offer the light on


def _home_confident(router_decision: Optional[dict]) -> bool:
    if not isinstance(router_decision, dict) or router_decision.get("gated"):
        return False
    if str(router_decision.get("routed") or router_decision.get("domain") or "").strip().lower() != "smart_home":
        return False
    conf = router_decision.get("head_conf")
    if not isinstance(conf, (int, float)):
        conf = router_decision.get("score")
    return isinstance(conf, (int, float)) and float(conf) >= HOME_CONFIDENT


def mid_conversation(panel_key: str, *, now: Optional[float] = None) -> bool:
    """True when this panel's previous turn was recent and NOT about a device: the person is talking about something else."""
    last = _LAST_TURN.get((panel_key or "-")[:80])
    if last is None:
        return False
    t = time.monotonic() if now is None else now
    return (t - last[0]) < MID_CONVERSATION_S and not last[1]


def note_turn(panel_key: str, *, device: bool, now: Optional[float] = None) -> None:
    key = (panel_key or "-")[:80]
    _LAST_TURN[key] = (time.monotonic() if now is None else now, bool(device))
    _LAST_TURN.move_to_end(key)
    while len(_LAST_TURN) > _MAX_PANELS:
        _LAST_TURN.popitem(last=False)


def reset() -> None:
    _LAST_TURN.clear()


def _claimed(text: str, context: Optional[dict]) -> bool:
    import stt_snap

    return stt_snap.claim_of(text, context) is not None


def decide(text: str, *, router_decision: Optional[dict] = None, panel_key: str = "-", context: Optional[dict] = None,
           claimed: Optional[Callable[[str, Optional[dict]], bool]] = None, now: Optional[float] = None) -> Optional[Ask]:
    """The question to ask for ``text``, or None. Records this turn on the panel's clock either way. Never raises."""
    try:
        busy = mid_conversation(panel_key, now=now)
        polarity = shape(text)                       # pure and cheap: most turns stop here, before any matcher runs
        if polarity is not None and (claimed or _claimed)(text, context):
            polarity = None                          # a matcher owns the words: they just run
        note_turn(panel_key, device=polarity is not None or _device_claim(text, context, claimed), now=now)
        if polarity is None or busy or _home_confident(router_decision):
            return None
        sec = _data()
        question = str((sec.get("question") or {}).get(polarity) or "").strip()
        command = str((sec.get("command") or {}).get(polarity) or "").strip()
        if not question or not command:
            return None
        return Ask(heard=text, polarity=polarity, question=question, command=command)
    except Exception as exc:  # noqa: BLE001 - the guard never breaks a turn
        logger.warning("device_confirm: decide failed (%s)", type(exc).__name__)
        return None


def _device_claim(text: str, context: Optional[dict], claimed: Optional[Callable[..., Any]]) -> bool:
    """Was a CLAIMED turn a device command (so a garbled retry right after it is not 'another conversation')?"""
    if claimed is not None:
        return False         # an injected matcher (tests) says nothing about the domain
    try:
        import skybridge_service

        sky = skybridge_service.classify_skybridge_intent(text, context)
        return sky is not None and sky.domain == "smart_home"
    except Exception:  # noqa: BLE001
        return False


def log_line(ask: Ask, outcome: str, *, guest: bool) -> None:
    """``DEVICE_CONFIRM to=<intent> outcome=<asked|refused_home|...>``; the words only on a member's line, never a guest's."""
    words = "" if guest else f" heard={ask.heard[:60]!r}"
    logger.warning("DEVICE_CONFIRM to=%s outcome=%s%s", ask.intent, outcome, words)
