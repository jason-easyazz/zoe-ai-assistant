"""A hearing note for the brain when the first words after a wake are short and nothing claimed them (``ZOE_UNCLEAR_TURN_NOTE``).

Measured 2026-11-28 over a month of wake turns at the panel: about one wake turn in five is a short command (most often the
light) that the speech model heard as a few unrelated words. No deterministic matcher claims the words, the turn falls to the
brain, and the brain - which carries what it knows about the member - answers the garble as if it were a confidence: it reads a
feeling into it or asks what is on the person's mind. The person only wanted the light, says it again, and it works. Cutting the
wake phrase out of the audio does not repair the hearing (it fixes as many clips as it breaks), so the fix is in the answer.

``noted`` is asked by the panel's voice lane just before the brain is dispatched - so every fast tier, the STT snap and the device
confirm guard have already passed on the words. When the turn is the first after a wake and its words have the ``shape`` of a
possible mishear (few words, not a question, not the person talking about themselves, no feeling in it), the lane passes
``hearing_note=True`` with THAT brain call (an explicit argument: nothing ambient, nothing a later turn can inherit), and
``block`` hands the Flue lane ONE delimited line that rides after the user's words: these words may be misheard; if they
do not plainly make sense, say you did not catch it and ask again, or ask whether a household command was meant; never read
feelings into them. The brain still decides: a short line that does make sense ("sing a song") is answered as before.

Never weakens a floor: the distress hand-off and the medical safety floor run BEFORE the brain turn is composed and never see
this note, and ``shape`` refuses any words the distress lexicon scores at all. The words that drive ``shape`` are data
(``lexicons_data/en.json`` "unclear_turn"). Pure (lexicon data and one flag read); never raises into a turn.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import command_shapes
import lexicons
from log_redaction import utterance_tag as _utag  # logs carry a tag, never turn text

logger = logging.getLogger(__name__)

FLAG = "ZOE_UNCLEAR_TURN_NOTE"
_TOKEN_RE = re.compile(r"[a-z0-9']+")

#: rides after the user's words inside the [MEMORY CONTEXT pair the sidecar already elides from every message but the newest
BLOCK = (
    "[MEMORY CONTEXT — hearing note; do not mention this block]\n"
    "The words above came from speech recognition straight after the wake word and matched no command, so they may be "
    "misheard. If they plainly make sense as something a person would say to you, answer as you normally would. If they do "
    "not, say you didn't catch that and ask them to say it again; if they sound like a household request (a light, a timer, "
    "music, the time, the weather), ask whether that is what they wanted. When the words are unclear, do not guess at a "
    "feeling behind them, do not ask what is on their mind, and do not bring up anything you remember about them.\n"
    "[END MEMORY CONTEXT]"
)

def enabled() -> bool:
    try:
        from typed_env import flag

        return bool(flag(FLAG))
    except Exception:  # noqa: BLE001 - an unreadable flag keeps the declared default (on)
        return True


def _words(sec: dict, key: str) -> frozenset:
    return frozenset(str(w).strip().lower() for w in (sec.get(key) or []) if str(w).strip())


def shape(text: str) -> bool:
    """True when ``text`` could be a misheard command: few words, not a question, nobody talking about themselves, no feeling."""
    try:
        sec = lexicons.load("en").get("unclear_turn") or {}
        raw = (text or "").strip()
        if not sec or not raw or "?" in raw:
            return False
        toks = _TOKEN_RE.findall(command_shapes.command_core(raw))
        if not (1 <= len(toks) <= int(sec.get("max_words") or 6)):
            return False
        if toks[0] in _words(sec, "question_openers") or _words(sec, "self_words").intersection(toks):
            return False
        if " ".join(toks) in _words(sec, "plain_lines"):
            return False
        import distress_handoff

        if distress_handoff.detect(raw).tier != "none":
            return False     # the floor's words are never called unclear (the hand-off has already answered them anyway)
        import emotional_turn_guard

        return not emotional_turn_guard.is_emotional_turn(raw)
    except Exception as exc:  # noqa: BLE001 - the note is optional; the turn is not
        logger.warning("unclear_turn: shape failed (%s)", type(exc).__name__)
        return False


def noted(text: str, *, wake: bool, panel: bool) -> bool:
    """Should the brain call for ``text`` carry the hearing note? True only for the first turn after a WAKE at the PANEL whose
    words have the ``shape``, with the flag on. Logs one text-free line when it does. Never raises."""
    try:
        if not (wake and panel and enabled() and shape(text)):
            return False
        logger.info("UNCLEAR_TURN noted %s", _utag(text))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("unclear_turn: noted failed (%s)", type(exc).__name__)
        return False


def block(hearing_note: object) -> str:
    """The hearing note for a brain call the voice lane flagged (``hearing_note=True``), or "" (not flagged, flag off)."""
    try:
        return BLOCK if hearing_note is True and enabled() else ""
    except Exception:  # noqa: BLE001
        return ""
