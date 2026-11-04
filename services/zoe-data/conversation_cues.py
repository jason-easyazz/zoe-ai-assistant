"""Conversation cues as DATA: small-talk toward Zoe and "Zoe just made an offer", read from ``lexicons_data/<lang>.json``.

Two questions the turn lanes used to answer with English regexes scattered over three modules:

* ``small_talk_remainder`` - is this turn a question about how ZOE is ("how are you", "I want to know how you are") or other
  chat aimed at her? Such a turn is conversation: not a canned greeting (``intent_router``), not a private request that needs a
  PIN (``voice_scope``). The lexicon section is ``"wellbeing"`` (``patterns``, ``small_talk``, ``frames``).
* ``ends_with_offer`` - does Zoe's last reply END in an offer ("how about the history of coffee?")? A short acceptance after one
  is an answer to the offer, so it must reach the brain with the conversation instead of the canned "Okay."
  (``intent_router.detect_and_extract_intent``). The lexicon section is ``"offer"`` (``cues``, ``tail_chars``).

No word of any language lives here. A language with no section contributes nothing (never guessed from English); the text's own
language is tried first, then every other language that has the section (a short "como estas" carries too few function words to
detect). Stdlib + ``lexicons`` only: importable from the hot voice path. NEVER raises.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Optional

import lexicons
from typed_env import env_float

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
#: what may be left of a turn once the small-talk phrase and its lead-in are taken out, before it stops being small talk
#: ("hey zoe, how are you doing today" leaves 'hey zoe today'): a longer remainder is a different request that merely
#: contains the phrase.
MAX_REMAINDER_TOKENS = 5


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").strip().lower())


@lru_cache(maxsize=None)
def _compiled(lang: str, section: str, key: str) -> tuple["re.Pattern[str]", ...]:
    sec = lexicons.load(lang).get(section) or {}
    out = []
    for frag in sec.get(key) or []:
        try:
            out.append(re.compile(rf"(?<!\w)(?:{frag})(?!\w)", re.IGNORECASE))
        except re.error:
            continue  # a fragment that does not compile contributes nothing
    return tuple(out)


def _langs(text: str, section: str) -> list[str]:
    first = lexicons.detect(text)
    order = [first] + [c for c in lexicons.LANGS if c != first]
    return [c for c in order if lexicons.load(c).get(section)]


def small_talk_remainder(text: str) -> Optional[str]:
    """``None`` when ``text`` is not small talk toward Zoe; otherwise what is left of it once the phrase and its lead-in are
    removed (``""`` for a bare "how are you"). The caller decides whether the remainder carries a personal request: this
    module owns the words, not the privacy rule."""
    try:
        t = _norm(text)
        if not t:
            return None
        for lang in _langs(t, "wellbeing"):
            phrases = _compiled(lang, "wellbeing", "patterns") + _compiled(lang, "wellbeing", "small_talk")
            hit = next((m for rx in phrases for m in [rx.search(t)] if m), None)
            if hit is None:
                continue
            rest = t[:hit.start()] + " " + t[hit.end():]
            for rx in _compiled(lang, "wellbeing", "frames"):
                rest = rx.sub(" ", rest)
            tokens = _TOKEN_RE.findall(rest)
            if len(tokens) > MAX_REMAINDER_TOKENS:
                return None
            return " ".join(tokens)
    except Exception:  # noqa: BLE001 - a cue lookup never breaks a turn
        return None
    return None


def is_wellbeing_question(text: str) -> bool:
    """True when ``text`` asks how Zoe is ("how are you", "how's it going", "are you okay"), with at most a greeting or her
    name around it. A wellbeing question is a conversational turn."""
    try:
        t = _norm(text)
        for lang in _langs(t, "wellbeing"):
            if any(rx.search(t) for rx in _compiled(lang, "wellbeing", "patterns")):
                return small_talk_remainder(t) is not None
    except Exception:  # noqa: BLE001
        return False
    return False


def smart_home_followup_ttl_s() -> float:
    """``ZOE_SMART_HOME_FOLLOWUP_TTL_S`` (default 60): how long a controlled device stays "it"."""
    try:
        return max(0.0, float(os.environ.get("ZOE_SMART_HOME_FOLLOWUP_TTL_S", "60")))
    except (TypeError, ValueError):
        return 60.0


_FILLER_SEP = r"[\s,.!?:;-]*"


@lru_cache(maxsize=None)
def _filler_patterns(lang: str) -> tuple[tuple["re.Pattern[str]", ...], tuple["re.Pattern[str]", ...], tuple[str, ...]]:
    sec = lexicons.load(lang).get("command_filler") or {}

    def build(frags: list, fmt: str) -> tuple["re.Pattern[str]", ...]:
        out = []
        for frag in sorted(frags or [], key=len, reverse=True):
            try:
                out.append(re.compile(fmt.format(frag=frag), re.IGNORECASE))
            except re.error:
                continue
        return tuple(out)

    lead = build(sec.get("lead"), "^" + _FILLER_SEP + r"(?:{frag})(?!\w)" + _FILLER_SEP)
    tail = build(sec.get("tail"), _FILLER_SEP + r"(?<!\w)(?:{frag})" + _FILLER_SEP + "$")
    return lead, tail, tuple(str(w).lower() for w in sec.get("not_after_lead") or [])


def _starts_with(text: str, frag: str) -> bool:
    try:
        return re.match(rf"(?:{frag})(?!\w)", text, re.IGNORECASE) is not None
    except re.error:
        return False


def strip_command_filler(text: str) -> str:
    """``text`` without its leading politeness / wake words and trailing "please" / "for me" ("Could you please turn off
    the lights for me" -> "turn off the lights"), or ``text`` unchanged when there is none, when nothing would be left,
    or when what follows the lead makes it a question or an offer rather than a command ("would you like ...", "can you
    hear me"). Case is kept. The words are the lexicon's (section ``command_filler``); NEVER raises."""
    try:
        raw = (text or "").strip()
        if not raw:
            return text
        for lang in _langs(raw, "command_filler"):
            lead, tail, not_after = _filler_patterns(lang)
            out, stripped_lead = raw, False
            changed = True
            while changed:
                changed = False
                for pattern in lead:
                    m = pattern.match(out)
                    if m and m.end() < len(out):
                        out, changed, stripped_lead = out[m.end():], True, True
                        break
            if stripped_lead and any(_starts_with(out, w) for w in not_after):
                continue
            changed = True
            while changed:
                changed = False
                for pattern in tail:
                    m = pattern.search(out)
                    if m and m.start() > 0:
                        out, changed = out[:m.start()], True
                        break
            out = out.strip()
            if out and out != raw:
                return out
    except Exception:  # noqa: BLE001
        return text
    return text


def smart_home_followup(text: str) -> Optional[str]:
    """``"on"`` / ``"off"`` / ``"reverse"`` when ``text`` is a bare pronoun command about the device just controlled ("turn it
    back on again", "switch them off", "turn it back"), else ``None``. The WHOLE utterance must be lexicon words (verbs,
    pronouns, on / off / back, ignorable fillers): "turn it up", "put it on my shopping list" and "turn it back on and play jazz"
    are not follow-ups. The caller supplies the target and the freshness window (it holds what was last controlled)."""
    try:
        s = _norm(text)
        if not s:
            return None
        toks = re.findall(r"[^\W_]+", s)
        for lang in _langs(s, "smart_home_followup"):
            sec = lexicons.load(lang)["smart_home_followup"]
            on, off = set(sec.get("on") or []), set(sec.get("off") or [])
            pron = set(sec.get("pronouns") or []) | set(sec.get("self_contained") or [])
            back = set(sec.get("back") or [])
            allowed = set(sec.get("verbs") or []) | pron | on | off | back
            fillers = set(sec.get("fillers") or [])
            core = [t for t in toks if t not in fillers]
            if not core or any(t not in allowed for t in core):
                continue
            has_on, has_off = any(t in on for t in core), any(t in off for t in core)
            has_pron, has_back = any(t in pron for t in core), any(t in back for t in core)
            if has_on == has_off and (has_on or not (has_pron and has_back)):
                continue          # both, or neither (and not the bare "turn it back")
            if not (has_pron or (has_back and (has_on or has_off))):
                continue
            return "on" if has_on else "off" if has_off else "reverse"
    except Exception:  # noqa: BLE001
        return None
    return None


def smart_home_state_answer(text: str) -> Optional[str]:
    """``"on"`` / ``"off"`` when ``text`` is ONLY the answer to "On or off?" ("on", "turn it on", "off please", "switch it off"):
    lexicon verbs / pronouns / fillers around exactly one of on / off, else ``None`` ("on the table", "turn it on and play jazz")."""
    try:
        s = _norm(text)
        if not s:
            return None
        toks = re.findall(r"[^\W_]+", s)
        for lang in _langs(s, "smart_home_followup"):
            sec = lexicons.load(lang)["smart_home_followup"]
            on, off = set(sec.get("on") or []), set(sec.get("off") or [])
            allowed = (set(sec.get("verbs") or []) | set(sec.get("pronouns") or []) | set(sec.get("self_contained") or [])
                       | on | off | set(sec.get("back") or []))
            core = [t for t in toks if t not in set(sec.get("fillers") or [])]
            if not core or any(t not in allowed for t in core):
                continue
            has_on, has_off = any(t in on for t in core), any(t in off for t in core)
            if has_on != has_off:
                return "on" if has_on else "off"
    except Exception:  # noqa: BLE001
        return None
    return None


def calendar_followup_ttl_s() -> float:
    """``ZOE_CALENDAR_FOLLOWUP_TTL_S`` (default 90): how long after a calendar answer "what is it" still means those events."""
    try:
        return max(0.0, float(os.environ.get("ZOE_CALENDAR_FOLLOWUP_TTL_S", "90")))
    except (TypeError, ValueError):
        return 90.0


def calendar_followup(text: str) -> bool:
    """True when ``text`` is a short question about the calendar answer just given ("what is it", "what are they", "which one",
    "what's the one event"). The WHOLE utterance must match a lexicon pattern once its lead-ins ("I was wondering", "can you
    tell me") and fillers are taken off the ends: "what is it about you" and "what time is it" are not follow-ups. The caller
    supplies the freshness window and does the (member-scoped) read."""
    try:
        s = _norm(text)
        s = re.sub(r"[.!?,;:]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            return False
        for lang in _langs(s, "calendar_followup"):
            sec = lexicons.load(lang)["calendar_followup"]
            lead = sorted((str(x).lower() for x in sec.get("lead_ins") or []), key=len, reverse=True)
            tail = sorted((str(x).lower() for x in sec.get("fillers") or []), key=len, reverse=True)
            core, changed = s, True
            while changed and core:
                changed = False
                for frag in lead:
                    if core == frag or core.startswith(frag + " "):
                        core, changed = core[len(frag):].strip(), True
                        break
                for frag in tail:
                    if core.endswith(" " + frag):
                        core, changed = core[: -len(frag)].strip(), True
                        break
            if core and any(re.fullmatch(frag, core) for frag in sec.get("patterns") or []):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def turn_continuation_ttl_s() -> float:
    """``ZOE_TURN_CONTINUATION_TTL_S`` (default 60): how long after a turn a bare "what else?" still follows that turn's domain."""
    return max(0.0, env_float("ZOE_TURN_CONTINUATION_TTL_S", 60.0))


def turn_continuation(text: str) -> bool:
    """True when ``text`` is a short CONTEXT-FREE continuation ("what else?", "and?", "tell me more", "anything else", "go on").
    It names no topic, so its own embedding is noise (live 2026-10-29: four "what else?" after a memory answer all routed to
    ``lists``); the caller makes it follow the previous turn. The WHOLE utterance must match a lexicon pattern once lead-ins and
    fillers are taken off the ends ("what else do you know about me" is a real question, not a continuation)."""
    try:
        s = _norm(text)
        s = re.sub(r"[.!?,;:]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            return False
        for lang in _langs(s, "turn_continuation"):
            sec = lexicons.load(lang)["turn_continuation"]
            lead = sorted((str(x).lower() for x in sec.get("lead_ins") or []), key=len, reverse=True)
            tail = sorted((str(x).lower() for x in sec.get("fillers") or []), key=len, reverse=True)
            pats = sec.get("patterns") or []
            if any(re.fullmatch(frag, s) for frag in pats):
                return True
            core, changed = s, True
            while changed and core:
                changed = False
                for frag in lead:
                    if core.startswith(frag + " "):
                        core, changed = core[len(frag):].strip(), True
                        break
                for frag in tail:
                    if core.endswith(" " + frag):
                        core, changed = core[: -len(frag)].strip(), True
                        break
            if core and any(re.fullmatch(frag, core) for frag in pats):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def ends_with_offer(reply: str) -> bool:
    """True when the END of Zoe's reply makes an offer ("how about ...?", "would you like ...?", "want me to ...?")."""
    try:
        text = (reply or "").strip()
        if not text:
            return False
        for lang in _langs(text, "offer"):
            sec = lexicons.load(lang).get("offer") or {}
            tail = text[-int(sec.get("tail_chars") or 240):]
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", tail) if s.strip()]
            window = " ".join(sentences[-2:]).replace("’", "'")
            if any(rx.search(window) for rx in _compiled(lang, "offer", "cues")):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False
