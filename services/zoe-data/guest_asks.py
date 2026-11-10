"""A stranger who asks Zoe to remember them, forget them, or say who they are gets the honest answer, never a false "I'll remember" (QA round 7).

THE MISS. A guest (nobody signed in, no recognised voice) said "hi. im quinn, remember me". The memory expert read the sentence as a fact to
store and answered "Got it - I'll remember hi. you're quinn, remember you." - a promise nothing keeps (the guest memory discard drops every
guest turn), garbled besides. "forget me" was deflected with "I'm here to listen", and "do you know whos talking" with "I'd need to check my
memory".

THE RULE. For an UNIDENTIFIED speaker (``user_filters.is_unidentified``) who is not a recognised named guest (``named_guests.current()`` is
None), three whole-utterance shapes are answered in code, from what is true:

* remember me ("remember me", "im quinn, remember me", "can you remember my name") -> guests are not remembered; Zoe can learn their voice if a
  household member asks her to; nothing was saved.
* forget me ("forget me", "delete me", "erase what you know about me") -> guests are not remembered, so there is nothing to forget.
* who is this ("do you know me", "do you know who's talking") -> she does not know them yet. ("who am i" is the identity card's, answered upstream.)

Pure shape check + one context read. A MEMBER is never touched (their "remember me" is the account's, their "forget me" the forget tier's).
Never raises.
"""
from __future__ import annotations

import re

REMEMBER_REPLY = ("I don't keep anything about guests, so I haven't saved that. If someone in the house asks me to learn your voice, "
                  "I'll know you next time.")
FORGET_REPLY = "I don't remember guests, so there's nothing of yours to forget."
WHO_REPLY = "I don't know you yet. I only know people the household has introduced me to."

_LEAD = r"(?:(?:hi|hey|hello|ok|okay|so|um|zoe|please|and|well)[\s,.!]+)*"
_INTRO = r"(?:(?:i'?m|im|i\s+am|my\s+name\s+is|this\s+is|it'?s|its)\s+[a-z][a-z'-]*[\s,.!]+)?"
_REMEMBER = re.compile(
    r"^" + _LEAD + _INTRO + _LEAD + r"(?:(?:can|could|will|would)\s+you\s+(?:please\s+)?)?(?:please\s+)?"
    r"(?:remember|know|recognise|recognize)\s+(?:me|who\s+i\s+am|my\s+name|my\s+voice|us)\s*(?:next\s+time|again|later|please)?[\s?.!]*$")
_FORGET = re.compile(
    r"^" + _LEAD + r"(?:(?:can|could|will|would)\s+you\s+(?:please\s+)?)?(?:please\s+)?(?:forget|delete|erase|remove)\s+"
    r"(?:me|all\s+about\s+me|everything\s+(?:about|on)\s+me|what\s+you\s+know\s+about\s+me|my\s+(?:name|details|info|information))"
    r"\s*(?:please)?[\s?.!]*$")
_WHO = re.compile(
    r"^" + _LEAD + r"(?:do\s+you\s+know\s+(?:me|who\s+i\s+am|who\s*(?:'?s|is|are)\s+(?:talking|speaking|this|there|that)|"
    r"my\s+name)|(?:do\s+you\s+)?know\s+who\s+(?:i\s+am|this\s+is)|who\s+(?:'?s|is)\s+(?:talking|speaking|this))[\s?.!]*$")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("’", "'")).strip()


def kind(text: str) -> str:
    """"remember" | "forget" | "who" | "" for the whole utterance. Pure."""
    t = _norm(text)
    if not t or len(t.split()) > 14:
        return ""
    if _FORGET.match(t):
        return "forget"
    if _REMEMBER.match(t):
        return "remember"
    if _WHO.match(t):
        return "who"
    return ""


def reply(text: str, user_id: str) -> str:
    """The honest answer for an unidentified, unrecognised speaker's remember / forget / who ask, else "". Never raises."""
    try:
        k = kind(text)
        if not k:
            return ""
        from user_filters import is_unidentified

        if not is_unidentified(user_id):
            return ""
        import named_guests

        if named_guests.current() is not None:
            return ""        # a recognised named guest: that flow (and its flags) owns the turn
        return {"remember": REMEMBER_REPLY, "forget": FORGET_REPLY, "who": WHO_REPLY}[k]
    except Exception:  # noqa: BLE001 - never breaks a turn
        return ""
