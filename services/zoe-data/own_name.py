"""own_name - the member's OWN name is never a contact to add.

Why (QA round 6b, 2026-11-09): about 8% of replies ended "Would you like me to add Walter (your customer) as a contact?" - Walter
being the person talking - up to four in a row, and "no" never cleared it. Every emitter of a ``person_create`` offer (the LLM
detector, the "my <relation> <Name>" regex, the backfill) names whoever the user's words mention, and the user introduces
themself in their own words ("its walter", "nadia here", "my name is Anika"). The only self-check was in the backfill and it
compared the NAME with the account's id/display name: a household member whose account name is a handle never matched, and the
detectors had no self-check at all.

One answer, used at both ends: ``names(user_id)`` is everything that is the speaker's own name (the account / preferred name, the
tokens of the account id, and the names they have introduced themself with, kept in ``user_preferences`` under
``introduced_names``); ``is_own(name, own)`` is the match (a full name, or a bare first name - "Walter Jr" is somebody else).
``pending_suggestions`` refuses to STORE such an offer and closes one already stored when it is read, so the offers that were
already sitting in a member's queue clear on the next turn.

Only a SUPPRESSION list: a wrong entry costs one missed contact offer, never a wrong fact. It is not identity (the identity wall in
``identity_facts`` is untouched). Never raises.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

PREF_KEY = "introduced_names"
MAX_NAMES = 5
_NAME = r"([A-Za-z][A-Za-z'’\-]{1,23})"
_LEAD = r"^\W*(?:(?:hey|hi|hello|hiya|morning|evening|g'?day|ok|okay|so|um|zoe)\b[\s,.!:;-]*)*"
_END = r"(?=\s*(?:[,.!?;:]|$|\s+(?:here|speaking|again|and|but|so|i\b|im\b|i'm\b|just)\b))"
# "its walter. ..." / "this is walter" / "nadia here" / "my name is anika" / "i'm walter" / "walter speaking" / "walter again"
_SELF_INTRO_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    _LEAD + r"(?:it'?s|its|it\s+is|this\s+is|that'?s\s+me,?)\s+" + _NAME + _END,
    _LEAD + _NAME + r"\s+(?:here|speaking|again)\b",
    r"\bmy\s+name(?:'s|\s+is)\s+(?:actually\s+)?" + _NAME,
    _LEAD + r"(?:i\s+am|i'?m|im)\s+" + _NAME + _END,
    # "hi zoe, anika. what happened with my team": a greeting to her, then the speaker's own name
    r"^\W*(?:hey|hi|hello|hiya)\s+zoe[\s,.!:;-]+" + _NAME + r"(?=\s*[.,!;:]|\s*$)",
))
#: words these patterns catch that are not names ("its cold", "im tired", "this is great")
_NOT_NAMES = frozenset((
    "a an the my your our his her their this that it its is are was be not no yes ok okay hi hey hello here there back home back "
    "fine good great bad well sorry tired hungry cold hot late early done ready sure right wrong just still also really very so "
    "too now then again today tonight tomorrow yesterday going gonna getting looking trying thinking wondering worried happy sad "
    "angry upset busy free alone old young new nice lovely kind cooking driving working watching making taking having doing "
    "thanks thank cheers please morning evening afternoon night what whats who how why when where can could would should will do does did can't cant dont don't zoe user guest me you him them us one two three four five six seven eight nine ten i im i'm ive i've id i'd ill i'll"
).split())


def introduced_name(message: str) -> str:
    """The name the speaker gave for THEMSELF in ``message`` ("its walter. what tablets..." -> "Walter"), or "". Pure."""
    text = " ".join((message or "").replace("’", "'").split())
    if not text or len(text) > 400:
        return ""
    for rx in _SELF_INTRO_RES:
        m = rx.search(text)
        if not m:
            continue
        name = m.group(1).strip("'-")
        if len(name) >= 2 and name.lower() not in _NOT_NAMES and not name.lower().endswith(("ing", "ly")):
            return name[:1].upper() + name[1:]
    return ""


async def note_introduction(user_id: str, message: str) -> str:
    """Remember a name the speaker introduced themself with (called on every real user turn). Returns the name noted, or "".
    Idempotent: a name already on the list is not rewritten. Never raises."""
    name = introduced_name(message)
    if not name or not user_id or user_id in ("guest", "voice-guest"):
        return ""
    try:
        from user_filters import is_unidentified

        if is_unidentified(user_id):
            return ""
        import user_prefs

        have = await user_prefs.get_pref(user_id, PREF_KEY, []) or []
        if not isinstance(have, list):
            have = []
        if name.lower() in {str(h).lower() for h in have}:
            return name
        await user_prefs.set_pref(user_id, PREF_KEY, (have + [name])[-MAX_NAMES:])
        return name
    except Exception as exc:  # noqa: BLE001 - a suppression list never costs the turn
        logger.debug("own_name: note failed (%s)", type(exc).__name__)
        return ""


async def names(user_id: str) -> frozenset:
    """Every folded name that is the speaker's own: full names AND first names. Empty when none is known. Never raises."""
    out: set = set()
    try:
        from people_utils import _fold_name

        raw: list = []
        try:
            import identity_facts

            ident = await identity_facts.resolve_identity(user_id, budget_s=1.0)
            if ident is not None:
                raw += [getattr(ident, "name", ""), getattr(ident, "account_name", "")]
        except Exception:  # noqa: BLE001
            pass
        try:
            import user_prefs

            intro = await user_prefs.get_pref(user_id, PREF_KEY, []) or []
            raw += [str(x) for x in intro] if isinstance(intro, list) else []
        except Exception:  # noqa: BLE001
            pass
        # a handle account ("jason", "jason.smith"): its alphabetic tokens; a generated id ("demo_bar_4285830f") yields tokens that
        # are not names, which only ever suppress an offer for somebody called "Bar"
        raw += [t for t in re.split(r"[^a-z]+", str(user_id or "").lower()) if len(t) >= 3]
        for r in raw:
            f = _fold_name(str(r or ""))
            if not f:
                continue
            out.add(f)
            out.add(f.split()[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("own_name: names failed (%s)", type(exc).__name__)
    return frozenset(out)


def is_own(name: str, own) -> bool:
    """Is ``name`` the speaker's own? A full name that equals one of theirs, or a BARE first name that is one of their first names
    (a two-word name that only shares a first name - "Walter Jr" - is somebody else). Pure."""
    try:
        from people_utils import _fold_name

        want = _fold_name(name or "")
    except Exception:  # noqa: BLE001
        return False
    if not want or not own:
        return False
    return want in own
